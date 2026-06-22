from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from wcpredict.names import same_team


ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

METRICS = {
    "ball possession": ("possession", "%"),
    "posesión": ("possession", "%"),
    "total shots": ("shots", "match"),
    "remates totales": ("shots", "match"),
    "shots on target": ("shots_on_target", "match"),
    "remates a puerta": ("shots_on_target", "match"),
    "corner kicks": ("corners", "match"),
    "saques de esquina": ("corners", "match"),
    "yellow cards": ("yellow_cards", "match"),
    "tarjetas amarillas": ("yellow_cards", "match"),
    "red cards": ("red_cards", "match"),
    "tarjetas rojas": ("red_cards", "match"),
    "expected goals": ("xg", "match"),
    "goles esperados": ("xg", "match"),
}


@dataclass(frozen=True)
class ScreenshotUpload:
    original_name: str
    mime_type: str
    content: bytes


@dataclass(frozen=True)
class StoredScreenshot:
    original_name: str
    mime_type: str
    byte_size: int
    sha256: str
    stored_path: Path


@dataclass(frozen=True)
class ExtractionCandidate:
    asset_id: int
    subject_type: str
    subject_name: str
    metric: str
    value_number: float
    value_text: str | None
    unit: str
    period: str
    raw_label: str
    raw_value: str
    confidence: float
    warnings: tuple[str, ...] = ()
    review_status: str = "pending_review"


PLAYER_METRICS = {
    "min": ("minutes", "minutes"),
    "minutes": ("minutes", "minutes"),
    "minutos": ("minutes", "minutes"),
    "rating": ("rating", "rating"),
    "calificacion": ("rating", "rating"),
    "goals": ("goals", "match"),
    "goles": ("goals", "match"),
    "assists": ("assists", "match"),
    "asistencias": ("assists", "match"),
    "shots": ("shots", "match"),
    "remates": ("shots", "match"),
    "shots on target": ("shots_on_target", "match"),
    "remates a puerta": ("shots_on_target", "match"),
    "accurate passes": ("pass_accuracy", "%"),
    "pases precisos": ("pass_accuracy", "%"),
    "key passes": ("key_passes", "match"),
    "pases clave": ("key_passes", "match"),
    "tackles": ("tackles_won", "match"),
    "entradas": ("tackles_won", "match"),
    "interceptions": ("interceptions", "match"),
    "intercepciones": ("interceptions", "match"),
    "yellow cards": ("yellow_cards", "match"),
    "tarjetas amarillas": ("yellow_cards", "match"),
}


def store_upload(upload: ScreenshotUpload, directory: Path) -> StoredScreenshot:
    if upload.mime_type not in ALLOWED_MIME:
        raise ValueError("Unsupported screenshot type")
    if not upload.content:
        raise ValueError("Screenshot is empty")
    digest = sha256(upload.content).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}{ALLOWED_MIME[upload.mime_type]}"
    if not path.exists():
        path.write_bytes(upload.content)
    return StoredScreenshot(
        upload.original_name,
        upload.mime_type,
        len(upload.content),
        digest,
        path,
    )


def _number(value: str) -> float:
    normalized = re.sub(r"[^0-9.,-]", "", value).replace(",", ".")
    if not normalized:
        raise ValueError("OCR value has no number")
    return float(normalized)


def classify_sofascore_tokens(
    tokens: list[tuple[str, float]],
    team_a: str,
    team_b: str,
    asset_id: int,
) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []
    for index, (text, label_confidence) in enumerate(tokens):
        definition = METRICS.get(text.casefold().strip())
        if definition is None or index + 2 >= len(tokens):
            continue
        metric, unit = definition
        for team, token in zip(
            (team_a, team_b), tokens[index + 1 : index + 3]
        ):
            raw_value, value_confidence = token
            try:
                value = _number(raw_value)
            except ValueError:
                continue
            candidates.append(
                ExtractionCandidate(
                    asset_id=asset_id,
                    subject_type="team",
                    subject_name=team,
                    metric=metric,
                    value_number=value,
                    value_text=None,
                    unit=unit,
                    period="ALL",
                    raw_label=text,
                    raw_value=raw_value,
                    confidence=min(label_confidence, value_confidence),
                )
            )
    return candidates


def _player_value(raw_value: str, metric: str) -> float:
    if metric == "pass_accuracy":
        percentages = re.findall(r"(-?\d+(?:[.,]\d+)?)\s*%", raw_value)
        if percentages:
            return float(percentages[-1].replace(",", "."))
    return _number(raw_value)


def classify_sofascore_player_table(
    headers: list[str],
    rows: list[list[tuple[str, float]]],
    team_name: str,
    asset_id: int,
) -> list[ExtractionCandidate]:
    definitions = [PLAYER_METRICS.get(header.casefold().strip()) for header in headers]
    candidates: list[ExtractionCandidate] = []
    for row in rows:
        if not row:
            continue
        player_name, player_confidence = row[0]
        if not player_name.strip() or len(row) < 2:
            continue
        for index in range(1, min(len(headers), len(row))):
            definition = definitions[index]
            if definition is None:
                continue
            metric, unit = definition
            raw_value, value_confidence = row[index]
            try:
                value = _player_value(raw_value, metric)
            except ValueError:
                continue
            confidence = min(player_confidence, value_confidence)
            warnings = ["parser:sofascore_player_table", f"team:{team_name}"]
            if confidence < 0.80:
                warnings.append("low_ocr_confidence")
            if metric == "rating" and not 0 <= value <= 10:
                warnings.append("rating_out_of_range")
            if metric == "minutes" and not 0 <= value <= 130:
                warnings.append("minutes_out_of_range")
            candidates.append(
                ExtractionCandidate(
                    asset_id=asset_id,
                    subject_type="player",
                    subject_name=player_name.strip(),
                    metric=metric,
                    value_number=value,
                    value_text=None,
                    unit=unit,
                    period="ALL",
                    raw_label=f"{team_name} | {headers[index]}",
                    raw_value=raw_value,
                    confidence=confidence,
                    warnings=tuple(warnings),
                )
            )
    return candidates


def classify_player_tables_from_ocr_rows(
    rows: list[list[tuple[str, float]]],
    team_a: str,
    team_b: str,
    asset_id: int,
) -> list[ExtractionCandidate]:
    current_team: str | None = None
    headers: list[str] | None = None
    candidates: list[ExtractionCandidate] = []
    for row in rows:
        texts = [text.strip() for text, _ in row if text.strip()]
        if not texts:
            continue
        combined = " ".join(texts)
        if same_team(combined, team_a):
            current_team, headers = team_a, None
            continue
        if same_team(combined, team_b):
            current_team, headers = team_b, None
            continue
        first = texts[0].casefold()
        if first in {"player", "jugador", "name", "nombre"} and any(
            PLAYER_METRICS.get(header.casefold()) for header in texts[1:]
        ):
            headers = texts
            continue
        if headers is not None:
            candidates.extend(
                classify_sofascore_player_table(
                    headers, [row], current_team or "Equipo por revisar", asset_id
                )
            )
    return candidates


def extract_ocr_tokens_and_rows(
    image_path: Path,
) -> tuple[list[tuple[str, float]], list[list[tuple[str, float]]]]:
    from rapidocr import RapidOCR

    engine = RapidOCR()
    result = engine(str(image_path))
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = getattr(result, "boxes", None)
    if texts is None or scores is None:
        return [], []
    tokens = [(str(text), float(score)) for text, score in zip(texts, scores)]
    if boxes is None:
        return tokens, []
    positioned = []
    for text, score, box in zip(texts, scores, boxes):
        points = list(box)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        positioned.append((sum(xs) / len(xs), sum(ys) / len(ys), max(ys) - min(ys), str(text), float(score)))
    grouped: list[dict] = []
    for x, y, height, text, score in sorted(positioned, key=lambda item: (item[1], item[0])):
        tolerance = max(6.0, height * 0.7)
        target = next((line for line in grouped if abs(float(line["y"]) - y) <= tolerance), None)
        if target is None:
            target = {"y": y, "items": []}
            grouped.append(target)
        target["items"].append((x, text, score))
        target["y"] = sum(item[0] for item in [(y,)] * len(target["items"])) / len(target["items"])
    table_rows = [
        [(text, score) for _, text, score in sorted(line["items"], key=lambda item: item[0])]
        for line in sorted(grouped, key=lambda item: item["y"])
    ]
    return tokens, table_rows


def extract_ocr_tokens(image_path: Path) -> list[tuple[str, float]]:
    tokens, _ = extract_ocr_tokens_and_rows(image_path)
    return tokens
