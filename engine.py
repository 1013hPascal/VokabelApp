import csv
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ----------------------------
# Konfiguration
# ----------------------------
DEFAULT_CONFIG = {
    "level_step": 2,
    "max_score": 6,
}


def load_config(config_path: str) -> Dict[str, int]:
    """
    config.csv Format:
    key,value
    level_step,2
    max_score,6
    """
    path = Path(config_path)
    cfg = dict(DEFAULT_CONFIG)

    if not path.exists():
        return cfg

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            k = (row.get("key") or "").strip()
            v = (row.get("value") or "").strip()
            if not k:
                continue
            try:
                cfg[k] = int(v)
            except ValueError:
                pass

    if cfg.get("level_step", 2) < 1:
        cfg["level_step"] = 1
    if cfg.get("max_score", 6) < 1:
        cfg["max_score"] = 6

    return cfg


# ----------------------------
# Datenmodelle
# ----------------------------
@dataclass
class VocabEntry:
    file_path: Path
    idx: int
    a: str
    b: str
    score: int


@dataclass
class VocabFileState:
    file_path: Path
    lang_a: str
    lang_b: str
    header_flag: str
    entries: List[VocabEntry]
    start_common_score: int
    target_score: int


# ----------------------------
# Hilfsfunktionen
# ----------------------------
def _clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


# ----------------------------
# CSV-Handling
# ----------------------------
def read_vocab_header(file_path: Path) -> Tuple[str, str]:
    """
    Liest nur die ersten zwei Header-Spalten (Sprache1, Sprache2).
    """
    if not file_path.exists():
        raise FileNotFoundError(f"CSV nicht gefunden: {file_path}")

    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)

    if not header or len(header) < 2:
        raise ValueError(f"CSV hat keinen gültigen Header (mind. 2 Spalten): {file_path}")

    lang_a = (header[0] or "").strip() or "Sprache 1"
    lang_b = (header[1] or "").strip() or "Sprache 2"
    return lang_a, lang_b


def read_vocab_csv(file_path: Path, max_score: int) -> Tuple[str, str, str, List[Tuple[str, str, int]]]:
    """
    Returns:
      lang_a, lang_b, flag(n/f), entries [(a,b,score)]
    """
    if not file_path.exists():
        raise FileNotFoundError(f"CSV nicht gefunden: {file_path}")

    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows or len(rows[0]) < 2:
        raise ValueError(f"CSV hat keinen gültigen Header (mind. 2 Spalten): {file_path}")

    header = rows[0]
    lang_a = (header[0] or "").strip() or "Sprache 1"
    lang_b = (header[1] or "").strip() or "Sprache 2"

    flag = "n"
    if len(header) >= 3:
        flag = (header[2] or "").strip().lower() or "n"
        if flag not in ("n", "f"):
            flag = "n"

    entries: List[Tuple[str, str, int]] = []
    for r in rows[1:]:
        if not r or len(r) < 2:
            continue
        a = str(r[0]).strip()
        b = str(r[1]).strip()
        if not a or not b:
            continue
        score = 0
        if len(r) >= 3:
            try:
                score = int(str(r[2]).strip())
            except ValueError:
                score = 0
        score = _clamp(score, 0, max_score)
        entries.append((a, b, score))

    if len(entries) < 1:
        raise ValueError(f"CSV enthält keine Vokabeln: {file_path}")

    return lang_a, lang_b, flag, entries


def normalize_and_load_file(file_path: Path, level_step: int, max_score: int, force_reset: bool) -> VocabFileState:
    """
    - Ergänzt Scores (0) falls fehlen
    - Wenn Scores nicht alle gleich: setzt alle auf 0
    - force_reset: setzt alle auf 0
    - Header-Flag: f wenn alle max_score, sonst n
    - Zielscore der Runde:
        - wenn Start max: reset -> Ziel = level_step
        - sonst Ziel = start + level_step (geclamped)
    """
    lang_a, lang_b, _flag, raw_entries = read_vocab_csv(file_path, max_score)

    scores = [s for _, _, s in raw_entries]
    all_equal = len(set(scores)) == 1

    if force_reset or (not all_equal):
        start_common = 0
        normalized_entries = [(a, b, 0) for (a, b, _) in raw_entries]
    else:
        start_common = scores[0] if scores else 0
        normalized_entries = raw_entries

    if start_common >= max_score:
        start_common = 0
        normalized_entries = [(a, b, 0) for (a, b, _) in normalized_entries]
        target = _clamp(level_step, 0, max_score)
    else:
        target = _clamp(start_common + level_step, 0, max_score)

    entries: List[VocabEntry] = []
    for i, (a, b, s) in enumerate(normalized_entries):
        entries.append(VocabEntry(file_path=file_path, idx=i, a=a, b=b, score=s))

    new_flag = "f" if all(e.score >= max_score for e in entries) else "n"

    state = VocabFileState(
        file_path=file_path,
        lang_a=lang_a,
        lang_b=lang_b,
        header_flag=new_flag,
        entries=entries,
        start_common_score=start_common,
        target_score=target,
    )

    # Persistieren, falls normalisiert/reset
    write_vocab_csv(state, max_score=max_score)
    return state


def write_vocab_csv(state: VocabFileState, max_score: int) -> None:
    flag = "f" if all(e.score >= max_score for e in state.entries) else "n"
    state.header_flag = flag

    rows = []
    rows.append([state.lang_a, state.lang_b, flag])
    entries_sorted = sorted(state.entries, key=lambda e: e.idx)
    for e in entries_sorted:
        rows.append([e.a, e.b, str(_clamp(e.score, 0, max_score))])

    with state.file_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


# ----------------------------
# Projekt-Scan
# ----------------------------
def list_course_folders(base_dir: str) -> List[str]:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in base.iterdir() if p.is_dir()])


def list_csv_files(course_folder: str) -> List[str]:
    folder = Path(course_folder)
    if not folder.exists():
        return []
    return sorted([p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"])


# ----------------------------
# Trainings-Session
# ----------------------------
class VocabSession:
    """
    Wörter werden random gewählt, aber nie zweimal direkt hintereinander (wenn möglich).
    Scoring:
      - richtig: +1
      - falsch (1. Fehler in Folge auf derselben Karte): +0
      - falsch (2. Fehler in Folge auf derselben Karte): -1 (min 0)

    direction:
      - "A2B"
      - "B2A"
      - "MIX" (zufällig pro Karte)
    """

    def __init__(self, file_states: List[VocabFileState], direction: str, level_step: int, max_score: int):
        self.file_states = file_states
        self.direction = direction  # "A2B" | "B2A" | "MIX"
        self.level_step = level_step
        self.max_score = max_score

        self.entries: Dict[Tuple[Path, int], VocabEntry] = {}
        self.targets: Dict[Path, int] = {}

        for fs in self.file_states:
            self.targets[fs.file_path] = fs.target_score
            for e in fs.entries:
                self.entries[(e.file_path, e.idx)] = e

        self.last_asked: Optional[Tuple[Path, int]] = None
        self.current: Optional[Tuple[Path, int]] = None
        self.current_direction: str = "A2B"
        self.wrong_streak: Dict[Tuple[Path, int], int] = {}

    def _eligible_keys(self) -> List[Tuple[Path, int]]:
        keys: List[Tuple[Path, int]] = []
        for k, e in self.entries.items():
            target = self.targets.get(e.file_path, self.max_score)
            if e.score < target:
                keys.append(k)
        return keys

    def is_finished(self) -> bool:
        return len(self._eligible_keys()) == 0

    def next_item(self) -> Optional[VocabEntry]:
        if self.is_finished():
            self.current = None
            return None

        candidates = self._eligible_keys()
        if self.last_asked in candidates and len(candidates) > 1:
            candidates = [c for c in candidates if c != self.last_asked]

        key = random.choice(candidates)
        self.current = key
        self.last_asked = key

        if self.direction == "MIX":
            self.current_direction = random.choice(["A2B", "B2A"])
        else:
            self.current_direction = self.direction

        return self.entries[key]

    def get_prompt_and_answer(self, entry: VocabEntry) -> Tuple[str, str]:
        d = self.current_direction if self.direction == "MIX" else self.direction
        if d == "A2B":
            return entry.a, entry.b
        else:
            return entry.b, entry.a

    def check_answer(self, user_input: str) -> Tuple[bool, str, str, int]:
        if self.current is None:
            raise RuntimeError("Keine aktuelle Vokabel aktiv.")

        entry = self.entries[self.current]
        _, solution = self.get_prompt_and_answer(entry)

        user_norm = (user_input or "").strip().lower()
        solution_norm = (solution or "").strip().lower()
        correct = (user_norm == solution_norm)

        if correct:
            entry.score = _clamp(entry.score + 1, 0, self.max_score)
            self.wrong_streak[self.current] = 0
        else:
            prev_wrong = self.wrong_streak.get(self.current, 0)
            if prev_wrong >= 1:
                entry.score = _clamp(entry.score - 1, 0, self.max_score)
            self.wrong_streak[self.current] = prev_wrong + 1

        return correct, user_norm, solution_norm, entry.score

    def persist(self) -> None:
        by_file: Dict[Path, List[VocabEntry]] = {}
        for (fp, _), e in self.entries.items():
            by_file.setdefault(fp, []).append(e)

        for fs in self.file_states:
            fs.entries = sorted(by_file.get(fs.file_path, []), key=lambda x: x.idx)
            write_vocab_csv(fs, max_score=self.max_score)


# ----------------------------
# High-level API für GUI
# ----------------------------
class VocabEngine:
    def __init__(self, base_dir: str = "Meine-Vokabeln", config_path: str = "config.csv"):
        import sys
        from pathlib import Path
        if getattr(sys, "frozen", False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent
        
        # Wenn ein expliziter Pfad übergeben wird (z.B. von der GUI), wird dieser genutzt, 
        # ansonsten wird er auf Basis des Standorts korrigiert.
        if not base_dir or base_dir == "Meine-Vokabeln":
            base_dir = str(base_path / "Meine-Vokabeln")
        
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        cfg = load_config(config_path)
        self.level_step = cfg.get("level_step", DEFAULT_CONFIG["level_step"])
        self.max_score = cfg.get("max_score", DEFAULT_CONFIG["max_score"])

    def list_courses(self) -> List[str]:
        return list_course_folders(str(self.base_dir))

    def list_sets(self, course_name: str) -> List[str]:
        course_dir = self.base_dir / course_name
        return list_csv_files(str(course_dir))

    def get_common_languages(self, course_name: str, selected_csvs: List[str]) -> Tuple[str, str]:
        """
        Liest Header-Sprachen. Wenn mehrere Dateien ausgewählt sind:
        - Wenn alle Header gleich sind: gib diese zurück.
        - Sonst: gib ("Sprache 1", "Sprache 2") zurück.
        """
        course_dir = self.base_dir / course_name
        langs: List[Tuple[str, str]] = []

        for fn in selected_csvs:
            fp = course_dir / fn
            try:
                langs.append(read_vocab_header(fp))
            except Exception:
                continue

        if not langs:
            return ("Sprache 1", "Sprache 2")

        first = langs[0]
        if all(l == first for l in langs):
            return first
        return ("Sprache 1", "Sprache 2")

    # ✅ neu: nur zum Anschauen (ohne Session)
    def load_vocab_pairs(self, course_name: str, selected_csvs: List[str]) -> List[Tuple[str, str]]:
        """
        Lädt alle (a,b)-Paare aus den gewählten CSVs (ohne Session/Scoring zu starten).
        """
        course_dir = self.base_dir / course_name
        pairs: List[Tuple[str, str]] = []

        for fn in selected_csvs:
            fp = course_dir / fn
            try:
                _lang_a, _lang_b, _flag, entries = read_vocab_csv(fp, max_score=self.max_score)
            except Exception:
                continue
            for a, b, _score in entries:
                pairs.append((a, b))

        return pairs

    def start_session(
        self,
        course_name: str,
        selected_csvs: List[str],
        direction: str,
        reset_progress: bool
    ) -> VocabSession:
        if direction not in ("A2B", "B2A", "MIX"):
            raise ValueError("Ungültige Richtung. Erlaubt: A2B, B2A oder MIX.")

        course_dir = self.base_dir / course_name
        if not course_dir.exists():
            raise FileNotFoundError(f"Kursordner nicht gefunden: {course_dir}")

        if not selected_csvs:
            raise ValueError("Keine CSV-Dateien ausgewählt.")

        file_states: List[VocabFileState] = []
        for fn in selected_csvs:
            fp = course_dir / fn
            fs = normalize_and_load_file(
                fp,
                level_step=self.level_step,
                max_score=self.max_score,
                force_reset=reset_progress
            )
            file_states.append(fs)

        return VocabSession(
            file_states=file_states,
            direction=direction,
            level_step=self.level_step,
            max_score=self.max_score
        )
