import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import httpx
from psycopg.types.json import Jsonb
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from common import db, migrate, setup, user

@asynccontextmanager
async def lifespan(app):
    migrate()
    yield

app=FastAPI(title="Run Scheduling Service",version="1.0.0",lifespan=lifespan)
setup(app)

class CreateRun(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    meetingPoint: str = Field(min_length=1,max_length=200)
    latitude: float = Field(ge=-90,le=90)
    longitude: float = Field(ge=-180,le=180)
    date: str
    time: str
    distanceKm: float = Field(gt=0,le=100)
    pace: str = Field(min_length=1,max_length=50)
    participantLimit: int = Field(ge=2,le=30)
    visibility: Literal["Public"] = "Public"


def view(connection,row,subject):
    participants=connection.execute("SELECT runner_id,attendance_confirmed FROM participants WHERE run_id=%s",(row["id"],)).fetchall()
    local=row["starts_at"].astimezone(ZoneInfo("Asia/Bangkok"))
    joined=any(str(p["runner_id"])==subject for p in participants)
    result={"id":str(row["id"]),"organizerId":str(row["organizer_id"]),"organizer":"You" if str(row["organizer_id"])==subject else "Runner",
      "title":row["title"],"area":row["meeting_point"],"meetingPoint":row["meeting_point"],
      "date":local.strftime("%Y-%m-%d"),"time":local.strftime("%H:%M"),"startsAt":row["starts_at"],"endsAt":row["ends_at"],
      "distanceKm":row["distance_km"],"pace":row["pace"],"participantLimit":row["participant_limit"],
      "latitude":round(row["latitude"],1),"longitude":round(row["longitude"],1),"visibility":"Public","participants":len(participants),"joined":joined,"status":row["status"],"weather":row["weather"]}
    # Exact pin is available only to joined participants.
    if joined:
        result.update(latitude=row["latitude"],longitude=row["longitude"],participantIds=[str(p["runner_id"]) for p in participants],
                      confirmedIds=[str(p["runner_id"]) for p in participants if p["attendance_confirmed"]])
    return result

def find(connection,run_id):
    row=connection.execute("SELECT * FROM runs WHERE id=%s FOR UPDATE",(run_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Run not found")
    return row

@app.post("/api/v1/runs",status_code=201)
def create(body: CreateRun,authorization: str=Header(default=""),claims=Depends(user)):
    try:
        starts=datetime.fromisoformat(body.date+"T"+body.time).replace(tzinfo=ZoneInfo("Asia/Bangkok"))
    except ValueError:
        raise HTTPException(422,"Invalid run date/time")
    if starts<=datetime.now(timezone.utc):
        raise HTTPException(422,"New run must start in the future")
    try:
        response=httpx.get(os.environ["CONDITIONS_URL"]+"/api/v1/conditions",params={"latitude":body.latitude,"longitude":body.longitude,"at":starts.isoformat()},headers={"Authorization":authorization},timeout=20)
        if response.status_code!=200:
            raise HTTPException(response.status_code,"Forecast check unavailable; choose an available forecast time")
        weather=response.json()
    except httpx.HTTPError:
        raise HTTPException(503,"Run Conditions unavailable")
    with db() as connection:
        run_id=uuid4()
        connection.execute("""INSERT INTO runs(id,organizer_id,title,meeting_point,latitude,longitude,starts_at,ends_at,distance_km,pace,participant_limit,weather)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (run_id,claims["sub"],body.title,body.meetingPoint,body.latitude,body.longitude,starts,starts+timedelta(hours=1),body.distanceKm,body.pace,body.participantLimit,Jsonb(weather)))
        connection.execute("INSERT INTO participants(run_id,runner_id) VALUES (%s,%s)",(run_id,claims["sub"]))
        return view(connection,find(connection,run_id),claims["sub"])

@app.get("/api/v1/runs")
def list_runs(claims=Depends(user)):
    with db() as connection:
        rows=connection.execute("SELECT * FROM runs WHERE starts_at>now() OR id IN (SELECT run_id FROM participants WHERE runner_id=%s) ORDER BY starts_at",(claims["sub"],)).fetchall()
        return [view(connection,row,claims["sub"]) for row in rows]

@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: UUID,claims=Depends(user)):
    with db() as connection:
        return view(connection,find(connection,run_id),claims["sub"])

@app.put("/api/v1/runs/{run_id}/participants/me")
def join(run_id: UUID,claims=Depends(user)):
    with db() as connection:
        row=find(connection,run_id)
        if row["starts_at"]<=datetime.now(timezone.utc):
            raise HTTPException(409,"Run has already started")
        existing=connection.execute("SELECT 1 FROM participants WHERE run_id=%s AND runner_id=%s",(run_id,claims["sub"])).fetchone()
        count=connection.execute("SELECT count(*) AS n FROM participants WHERE run_id=%s",(run_id,)).fetchone()["n"]
        if not existing and count>=row["participant_limit"]:
            raise HTTPException(409,"Run is full")
        connection.execute("INSERT INTO participants(run_id,runner_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",(run_id,claims["sub"]))
        return {"id":str(run_id),"joined":True}

@app.delete("/api/v1/runs/{run_id}/participants/me",status_code=204)
def leave(run_id: UUID,claims=Depends(user)):
    with db() as connection:
        row=find(connection,run_id)
        if str(row["organizer_id"])==claims["sub"]:
            raise HTTPException(409,"Organizer must stay in the run")
        if row["starts_at"]<=datetime.now(timezone.utc):
            raise HTTPException(409,"Run has already started")
        connection.execute("DELETE FROM participants WHERE run_id=%s AND runner_id=%s",(run_id,claims["sub"]))

@app.post("/api/v1/runs/{run_id}/attendance-confirmation")
def confirm(run_id: UUID,claims=Depends(user)):
    with db() as connection:
        row=find(connection,run_id)
        if row["ends_at"]>datetime.now(timezone.utc):
            raise HTTPException(409,"Run has not ended")
        changed=connection.execute("UPDATE participants SET attendance_confirmed=true WHERE run_id=%s AND runner_id=%s RETURNING runner_id",(run_id,claims["sub"])).fetchone()
        if not changed:
            raise HTTPException(403,"Only run participants can confirm attendance")
        count=connection.execute("SELECT count(*) AS n FROM participants WHERE run_id=%s AND attendance_confirmed",(run_id,)).fetchone()["n"]
        connection.execute("UPDATE runs SET status=%s WHERE id=%s",("completed" if count>=2 else "attendance_open",run_id))
        return view(connection,find(connection,run_id),claims["sub"])
