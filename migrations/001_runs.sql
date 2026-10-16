CREATE TABLE runs (
 id uuid PRIMARY KEY, organizer_id uuid NOT NULL, title text NOT NULL,
 meeting_point text NOT NULL, latitude double precision NOT NULL, longitude double precision NOT NULL,
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 distance_km double precision NOT NULL CHECK(distance_km>0), pace text NOT NULL,
 participant_limit integer NOT NULL CHECK(participant_limit BETWEEN 2 AND 30),
 status text NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','attendance_open','completed')),
 weather jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE participants (
 run_id uuid NOT NULL REFERENCES runs(id), runner_id uuid NOT NULL,
 attendance_confirmed boolean NOT NULL DEFAULT false,
 PRIMARY KEY(run_id,runner_id)
);
