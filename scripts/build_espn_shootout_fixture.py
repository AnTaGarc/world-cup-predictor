from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import requests


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wcpredict.espn_shootouts import parse_shootout_shots
from wcpredict.names import canonical_team_name


FIELDS = (
    "played_on", "competition", "competition_edition", "round_name", "team_a",
    "team_b", "winner_team", "sequence_number", "team_name", "player_name",
    "goalkeeper_name", "outcome", "source_provider", "source_url",
    "source_row_key", "retrieved_at_utc",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye el fixture auditable de tandas desde ESPN.")
    parser.add_argument("--events", default=str(ROOT / "data/fixtures/active_team_shootout_events.csv"))
    parser.add_argument("--output", default=str(ROOT / "data/fixtures/active_team_shootout_kicks.csv"))
    parser.add_argument("--cache-dir", default=str(ROOT / "data/cache/espn_shootouts"))
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with Path(args.events).open(encoding="utf-8-sig", newline="") as handle:
        events = list(csv.DictReader(handle))

    output: list[dict] = []
    for event in events:
        event_id = event["espn_event_id"]
        cache_path = cache_dir / f"{event_id}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{event['espn_league']}/summary?event={event_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            payload = response.json()
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        shots = parse_shootout_shots(payload)
        if not shots:
            raise ValueError(f"ESPN event {event_id} has no shootout shots")
        source_url = f"https://www.espn.com/soccer/match/_/gameId/{event_id}"
        for shot in shots:
            output.append({
                **{key: event.get(key, "") for key in FIELDS},
                **shot,
                "team_a": canonical_team_name(event["team_a"]),
                "team_b": canonical_team_name(event["team_b"]),
                "winner_team": canonical_team_name(event["winner_team"]),
                "team_name": canonical_team_name(shot["team_name"]),
                "source_provider": "espn",
                "source_url": source_url,
                "source_row_key": f"espn:{event_id}:{shot['event_id']}",
            })

    with Path(args.output).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(
            {key: row.get(key, "") for key in FIELDS}
            for row in output
        )
    print(f"Tandas: {len(events)}; lanzamientos: {len(output)}; salida: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
