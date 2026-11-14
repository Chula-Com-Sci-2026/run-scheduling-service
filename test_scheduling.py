from datetime import datetime,timedelta,timezone
from uuid import uuid4
from common import db
from conftest import auth
from psycopg.types.json import Jsonb

def seed(subject,other=None,past=False,limit=2,starts=None):
    run=str(uuid4())
    starts=starts or datetime.now(timezone.utc)+timedelta(days=-1 if past else 1)
    with db() as connection:
        connection.execute('INSERT INTO runs(id,organizer_id,title,meeting_point,latitude,longitude,starts_at,ends_at,distance_km,pace,participant_limit,weather) VALUES (%s,%s,%s,%s,13.73,100.541,%s,%s,5,%s,%s,%s)',(run,subject,'Test run','Park',starts,starts+timedelta(hours=1),'6',limit,Jsonb({})))
        connection.execute('INSERT INTO participants(run_id,runner_id) VALUES (%s,%s)',(run,subject))
        if other:connection.execute('INSERT INTO participants(run_id,runner_id) VALUES (%s,%s)',(run,other))
    return run

def test_membership_capacity_and_privacy(client,subject):
    run=seed(subject);second=str(uuid4());third=str(uuid4())
    assert client.get('/api/v1/runs').status_code==401
    public=client.get('/api/v1/runs/'+run,headers=auth(second)).json()
    assert 'participantIds' not in public
    assert (public['latitude'],public['longitude'])==(13.73,100.54)
    assert client.get('/api/v1/runs/'+run,headers=auth(subject)).json()['longitude']==100.541
    assert client.put(f'/api/v1/runs/{run}/participants/me',headers=auth(second)).status_code==200
    assert client.put(f'/api/v1/runs/{run}/participants/me',headers=auth(third)).status_code==409
    assert client.delete(f'/api/v1/runs/{run}/participants/me',headers=auth(subject)).status_code==409
    assert client.delete(f'/api/v1/runs/{run}/participants/me',headers=auth(second)).status_code==204

def test_two_confirmations_required_for_completion(client,subject):
    other=str(uuid4());run=seed(subject,other,past=True)
    one=client.post(f'/api/v1/runs/{run}/attendance-confirmation',headers=auth(subject))
    assert one.status_code==200 and one.json()['status']=='attendance_open'
    assert client.post(f'/api/v1/runs/{run}/attendance-confirmation',headers=auth(str(uuid4()))).status_code==403
    two=client.post(f'/api/v1/runs/{run}/attendance-confirmation',headers=auth(other))
    assert two.json()['status']=='completed' and len(two.json()['confirmedIds'])==2
    future=seed(subject)
    assert client.post(f'/api/v1/runs/{future}/attendance-confirmation',headers=auth(subject)).status_code==409

def test_organizer_finishes_a_started_run_to_open_attendance(client,subject):
    other=str(uuid4());run=seed(subject,other,starts=datetime.now(timezone.utc)-timedelta(minutes=5))
    assert client.post(f'/api/v1/runs/{run}/attendance-confirmation',headers=auth(other)).status_code==409
    assert client.post(f'/api/v1/runs/{run}/finish',headers=auth(other)).status_code==403
    finished=client.post(f'/api/v1/runs/{run}/finish',headers=auth(subject))
    assert finished.status_code==200 and datetime.fromisoformat(finished.json()['endsAt'])<=datetime.now(timezone.utc)
    assert client.post(f'/api/v1/runs/{run}/finish',headers=auth(subject)).status_code==200
    assert client.post(f'/api/v1/runs/{run}/attendance-confirmation',headers=auth(other)).status_code==200
    future=seed(subject)
    assert client.post(f'/api/v1/runs/{future}/finish',headers=auth(subject)).status_code==409

def test_invalid_new_run_and_provider_failure(client,subject,monkeypatch):
    body={'title':'Run','meetingPoint':'Park','latitude':13.73,'longitude':100.541,'date':(datetime.now()+timedelta(days=1)).strftime('%Y-%m-%d'),'time':'18:00','distanceKm':5,'pace':'6','participantLimit':2}
    import httpx
    monkeypatch.setattr(httpx,'get',lambda *a,**kw: httpx.Response(503,json={}))
    assert client.post('/api/v1/runs',json=body,headers=auth(subject)).status_code==503
    assert client.post('/api/v1/runs',json={**body,'date':'invalid'},headers=auth(subject)).status_code==422
