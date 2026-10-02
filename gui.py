import sys
import traceback
import csv
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QVBoxLayout, QHBoxLayout, QMessageBox,
    QStackedLayout, QMainWindow, QInputDialog, QMenu
)

from engine import VocabEngine, VocabSession, VocabEntry


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vokabeltrainer")

        # Pfad merken, weil wir config.csv aus dem Menü heraus schreiben wollen
        import sys
        from pathlib import Path
        base_path = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        self.config_path = str(base_path / "config.csv")
        self.engine = VocabEngine(base_dir=str(base_path / "Meine-Vokabeln"), config_path=self.config_path)

        self.course_name: Optional[str] = None
        self.selected_csvs: List[str] = []
        self.direction: str = "A2B"  # "A2B" | "B2A" | "MIX"
        self.reset_progress: bool = False

        self.lang_a: str = "Sprache 1"
        self.lang_b: str = "Sprache 2"

        self.session: Optional[VocabSession] = None
        self.current_entry: Optional[VocabEntry] = None
        self.current_prompt: str = ""
        self.current_solution: str = ""

        # Für Auto-Rücksprung nach "Richtig"
        self._pending_focus_timer: Optional[QTimer] = None

        # Für Checkbox-"Bounce"
        self._bounce_timer: Optional[QTimer] = None
        self._bounce_target: Optional[Tuple[QListWidget, QListWidgetItem]] = None
        self._dummy_map: Dict[QListWidget, QLineEdit] = {}

        # Start-Fokus nur einmal setzen
        self._did_initial_focus = False

        # ---------- Menü ----------
        self._build_menu()

        # ---------- Central Widget + Stack ----------
        self.central = QWidget()
        self.setCentralWidget(self.central)

        self.stack = QStackedLayout()
        self.central.setLayout(self.stack)

        self.screen_course = self.build_screen_course()
        self.screen_sets = self.build_screen_sets()
        self.screen_direction = self.build_screen_direction()
        self.screen_view = self.build_screen_view()   # Vorschau
        self.screen_learn = self.build_screen_learn()
        self.screen_done = self.build_screen_done()

        self.stack.addWidget(self.screen_course)
        self.stack.addWidget(self.screen_sets)
        self.stack.addWidget(self.screen_direction)
        self.stack.addWidget(self.screen_view)
        self.stack.addWidget(self.screen_learn)
        self.stack.addWidget(self.screen_done)

        self.stack.setCurrentWidget(self.screen_course)

        # Dummies pro Liste
        self._dummy_map = {
            self.lw_courses: self._dummy_course,
            self.lw_sets: self._dummy_sets,
            self.lw_direction: self._dummy_direction,
        }

        # ----------------------------
        # Globale Shortcuts (Alt+...)
        # ----------------------------
        QShortcut(QKeySequence("Alt+W"), self, activated=self.shortcut_weiter)
        QShortcut(QKeySequence("Alt+B"), self, activated=self.shortcut_beenden)  # global quit wie bisher
        QShortcut(QKeySequence("Alt+S"), self, activated=self.shortcut_starten)

        # Startup
        self.refresh_course_list()

    # ----------------------------
    # Menü
    # ----------------------------
    def _build_menu(self):
        # Alt+L öffnet über "&Lernmodus" i.d.R. automatisch (Plattform abhängig)
        menu_lernmodus = self.menuBar().addMenu("&Lernmodus")

        menu_settings = QMenu("Einstellungen", self)
        menu_lernmodus.addMenu(menu_settings)

        act_level_step = QAction("Richtige Antworten bis zum nächsten Level…", self)
        act_level_step.triggered.connect(self.menu_set_level_step)
        menu_settings.addAction(act_level_step)

        act_max_score = QAction("Maximaler Score…", self)
        act_max_score.triggered.connect(self.menu_set_max_score)
        menu_settings.addAction(act_max_score)

    def _write_config(self, level_step: Optional[int] = None, max_score: Optional[int] = None):
        # Werte aus Engine (oder override)
        ls = int(level_step if level_step is not None else self.engine.level_step)
        ms = int(max_score if max_score is not None else self.engine.max_score)

        # Minimal robust
        if ls < 1:
            ls = 1
        if ms < 1:
            ms = 1

        # In Datei schreiben (key,value)
        path = Path(self.config_path)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["key", "value"])
            w.writerow(["level_step", str(ls)])
            w.writerow(["max_score", str(ms)])

        # Engine live aktualisieren
        self.engine.level_step = ls
        self.engine.max_score = ms

    def menu_set_level_step(self):
        # Auswahl-Liste (du kannst den Bereich hier leicht erhöhen)
        options = [str(i) for i in range(1, 21)]
        current = str(int(self.engine.level_step))

        val, ok = QInputDialog.getItem(
            self,
            "Einstellung",
            "Richtige Antworten bis zum nächsten Level (level_step):",
            options,
            options.index(current) if current in options else 1,
            False,
        )
        if not ok:
            return
        try:
            ls = int(val)
        except ValueError:
            return

        self._write_config(level_step=ls)

        # Falls du gerade im Done-Screen bist, Label neu setzen (optional, aber hilfreich)
        if self.current_screen() is self.screen_done:
            self.show_done_screen()

        # Falls du im Set-Screen bist, Level-Anzeige aktualisieren
        if self.current_screen() is self.screen_sets and self.course_name:
            self.refresh_sets_list()

    def menu_set_max_score(self):
        options = [str(i) for i in (10, 20, 30, 50, 100, 200, 500)]
        current = str(int(self.engine.max_score))

        val, ok = QInputDialog.getItem(
            self,
            "Einstellung",
            "Maximaler Score (max_score):",
            options,
            options.index(current) if current in options else (options.index("100") if "100" in options else 0),
            False,
        )
        if not ok:
            return
        try:
            ms = int(val)
        except ValueError:
            return

        self._write_config(max_score=ms)

        # Anzeige-Refresh (Set-Liste zeigt Level an)
        if self.current_screen() is self.screen_sets and self.course_name:
            self.refresh_sets_list()

    # ----------------------------
    # Startup Focus: nach show()
    # ----------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_initial_focus:
            self._did_initial_focus = True
            QTimer.singleShot(0, self._startup_focus_course_dummy_then_first)

    def _startup_focus_course_dummy_then_first(self):
        self._dummy_course.setFocus(Qt.OtherFocusReason)
        QApplication.processEvents()
        QTimer.singleShot(25, self._focus_first_course_checkbox)

    def _focus_first_course_checkbox(self):
        if self.stack.currentWidget() is not self.screen_course:
            return
        if self.lw_courses.count() > 0:
            self.lw_courses.setFocus()
            self.lw_courses.setCurrentRow(0)
            it = self.lw_courses.item(0)
            if it is not None:
                self.lw_courses.setCurrentItem(it)
                self.lw_courses.scrollToItem(it)

    def _focus_first_sets_checkbox(self):
        if self.stack.currentWidget() is not self.screen_sets:
            return
        if self.lw_sets.count() > 0:
            self.lw_sets.setFocus()
            self.lw_sets.setCurrentRow(0)
            it = self.lw_sets.item(0)
            if it is not None:
                self.lw_sets.setCurrentItem(it)
                self.lw_sets.scrollToItem(it)

    # ----------------------------
    # Utilities
    # ----------------------------
    def set_screen(self, w: QWidget):
        self.stack.setCurrentWidget(w)

    def listwidget_items_checked(self, lw: QListWidget) -> List[str]:
        out = []
        for i in range(lw.count()):
            it = lw.item(i)
            if it.checkState() == Qt.Checked:
                data = it.data(Qt.UserRole)
                out.append(data if isinstance(data, str) and data else it.text())
        return out

    def current_screen(self) -> QWidget:
        return self.stack.currentWidget()

    def _cancel_pending_focus_timer(self):
        if self._pending_focus_timer is not None:
            try:
                self._pending_focus_timer.stop()
            except Exception:
                pass
            self._pending_focus_timer = None

    def _cancel_bounce_timer(self):
        if self._bounce_timer is not None:
            try:
                self._bounce_timer.stop()
            except Exception:
                pass
            self._bounce_timer = None
            self._bounce_target = None

    def _read_common_score_from_csv(self, fp: Path) -> int:
        if not fp.exists():
            return 0
        try:
            with fp.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
        except Exception:
            return 0

        if not rows or len(rows[0]) < 2:
            return 0

        scores: List[int] = []
        for r in rows[1:]:
            if not r or len(r) < 2:
                continue
            a = str(r[0]).strip()
            b = str(r[1]).strip()
            if not a or not b:
                continue
            s = 0
            if len(r) >= 3:
                try:
                    s = int(str(r[2]).strip())
                except ValueError:
                    s = 0
            s = max(0, min(s, self.engine.max_score))
            scores.append(s)

        if not scores:
            return 0
        if len(set(scores)) == 1:
            return scores[0]
        return 0

    def _score_to_level(self, score: int) -> int:
        step = self.engine.level_step if self.engine.level_step > 0 else 1
        return int(score) // step

    # ----------------------------
    # Checkbox Bounce (Dummy-Fokus -> zurück)
    # ----------------------------
    def _checkbox_bounce_focus(self, lw: QListWidget, item: QListWidgetItem, ms: int = 1000):
        self._cancel_bounce_timer()

        dummy = self._dummy_map.get(lw)
        if dummy is None:
            return

        lw.setCurrentItem(item)
        lw.scrollToItem(item)

        dummy.setFocus(Qt.OtherFocusReason)
        QApplication.processEvents()

        self._bounce_target = (lw, item)

        t = QTimer(self)
        t.setSingleShot(True)

        def back():
            self._bounce_timer = None
            target = self._bounce_target
            self._bounce_target = None
            if not target:
                return
            lw2, it2 = target
            lw2.setFocus()
            lw2.setCurrentItem(it2)
            lw2.scrollToItem(it2)
            lw2.repaint()
            lw2.viewport().update()

        t.timeout.connect(back)
        self._bounce_timer = t
        t.start(ms)

    def _refresh_accessibility_after_check(self, lw: QListWidget, item: QListWidgetItem):
        lw.repaint()
        lw.viewport().update()
        QApplication.processEvents()

        def reselect_then_bounce():
            lw.setCurrentItem(item)
            lw.scrollToItem(item)
            lw.repaint()
            lw.viewport().update()
            self._checkbox_bounce_focus(lw, item, ms=25)

        QTimer.singleShot(0, reselect_then_bounce)

    # ----------------------------
    # Shortcuts (Alt+...)
    # ----------------------------
    def shortcut_weiter(self):
        scr = self.current_screen()
        if scr is self.screen_learn:
            if self.btn_next.isEnabled():
                self.btn_next.click()
        elif scr is self.screen_done:
            self.btn_continue.click()

    def shortcut_beenden(self):
        self.on_quit_app()

    def shortcut_starten(self):
        scr = self.current_screen()
        if scr is self.screen_direction:
            self.btn_start.click()

    # ----------------------------
    # Screen 1: Kursordner wählen
    # ----------------------------
    def build_screen_course(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        lbl = QLabel("Kursordner auswählen (genau 1)")
        self.lw_courses = QListWidget()
        self.lw_courses.setAccessibleName("Kursordner")

        self._dummy_course = QLineEdit()
        self._dummy_course.setText(" ")
        self._dummy_course.setAccessibleName(" ")
        self._dummy_course.setReadOnly(True)
        self._dummy_course.setFocusPolicy(Qt.NoFocus)
        self._dummy_course.setStyleSheet(
            "border: 0px; background: transparent; color: transparent; selection-background-color: transparent;"
        )

        self.btn_course_select = QPushButton("Auswählen")
        self.btn_course_select.setAccessibleName("Kursordner auswählen")
        self.btn_course_select.clicked.connect(self.on_course_select)
        self.btn_course_select.setDefault(True)
        self.btn_course_select.setAutoDefault(True)

        layout.addWidget(lbl)
        layout.addWidget(self.lw_courses)
        layout.addWidget(self._dummy_course)
        layout.addWidget(self.btn_course_select)

        return w

    def refresh_course_list(self):
        try:
            self.lw_courses.itemChanged.disconnect()
        except Exception:
            pass

        self.lw_courses.clear()
        courses = self.engine.list_courses()
        if not courses:
            self.lw_courses.addItem(QListWidgetItem("Keine Ordner in 'Meine-Vokabeln' gefunden."))
            self.btn_course_select.setEnabled(False)
            return

        self.btn_course_select.setEnabled(True)
        for c in courses:
            it = QListWidgetItem(c)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Unchecked)
            self.lw_courses.addItem(it)

        self.lw_courses.itemChanged.connect(self.on_courses_item_changed)
        self.lw_courses.setCurrentRow(0)

    def on_courses_item_changed(self, item: QListWidgetItem):
        self._refresh_accessibility_after_check(self.lw_courses, item)

    def on_course_select(self):
        checked = self.listwidget_items_checked(self.lw_courses)
        if len(checked) != 1:
            QMessageBox.warning(self, "Auswahl", "Bitte genau einen Kursordner auswählen.")
            return

        self.course_name = checked[0]
        self.refresh_sets_list()
        self.set_screen(self.screen_sets)
        QTimer.singleShot(0, self._focus_first_sets_checkbox)

    # ----------------------------
    # Screen 2: CSVs wählen
    # ----------------------------
    def build_screen_sets(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        lbl = QLabel("Vokabel-Listen auswählen (Mehrfachauswahl möglich)")
        self.lw_sets = QListWidget()
        self.lw_sets.setAccessibleName("Vokabeldateien")

        self._dummy_sets = QLineEdit()
        self._dummy_sets.setText(" ")
        self._dummy_sets.setAccessibleName(" ")
        self._dummy_sets.setReadOnly(True)
        self._dummy_sets.setFocusPolicy(Qt.NoFocus)
        self._dummy_sets.setStyleSheet(
            "border: 0px; background: transparent; color: transparent; selection-background-color: transparent;"
        )

        row = QHBoxLayout()

        self.btn_sets_select = QPushButton("Vokabeln auswählen")
        self.btn_sets_select.setAccessibleName("Vokabeln auswählen")
        self.btn_sets_select.clicked.connect(self.on_sets_select)
        self.btn_sets_select.setDefault(True)
        self.btn_sets_select.setAutoDefault(True)

        self.btn_sets_back = QPushButton("Zurück")
        self.btn_sets_back.setAccessibleName("Zurück")
        self.btn_sets_back.clicked.connect(self.on_sets_back)

        row.addWidget(self.btn_sets_select)
        row.addWidget(self.btn_sets_back)

        layout.addWidget(lbl)
        layout.addWidget(self.lw_sets)
        layout.addWidget(self._dummy_sets)
        layout.addLayout(row)

        return w

    def on_sets_back(self):
        self.set_screen(self.screen_course)
        QTimer.singleShot(0, self._focus_first_course_checkbox)

    def refresh_sets_list(self):
        try:
            self.lw_sets.itemChanged.disconnect()
        except Exception:
            pass

        self.lw_sets.blockSignals(True)
        self.lw_sets.clear()

        if not self.course_name:
            self.lw_sets.blockSignals(False)
            return

        files = self.engine.list_sets(self.course_name)

        it_all = QListWidgetItem("Alle Vokabeln")
        it_all.setFlags(it_all.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it_all.setCheckState(Qt.Unchecked)
        self.lw_sets.addItem(it_all)

        course_dir = Path(self.engine.base_dir) / self.course_name

        for fn in files:
            fp = course_dir / fn
            score = self._read_common_score_from_csv(fp)
            level = self._score_to_level(score)

            display = f"{fn} (Level {level})"
            it = QListWidgetItem(display)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole, fn)
            self.lw_sets.addItem(it)

        self.lw_sets.blockSignals(False)
        self.lw_sets.itemChanged.connect(self.on_sets_item_changed)
        self.lw_sets.setCurrentRow(0)

    def _clear_sets_selection(self):
        try:
            self.lw_sets.itemChanged.disconnect()
        except Exception:
            pass

        self.lw_sets.blockSignals(True)
        for i in range(self.lw_sets.count()):
            it = self.lw_sets.item(i)
            if it is not None and (it.flags() & Qt.ItemIsUserCheckable):
                it.setCheckState(Qt.Unchecked)
        self.lw_sets.blockSignals(False)

        self.lw_sets.itemChanged.connect(self.on_sets_item_changed)

        self.selected_csvs = []
        self.reset_progress = False

        self.lw_sets.setCurrentRow(0)
        QApplication.processEvents()

    def on_sets_item_changed(self, item: QListWidgetItem):
        if item.text() == "Alle Vokabeln":
            state = item.checkState()
            self.lw_sets.blockSignals(True)
            for i in range(1, self.lw_sets.count()):
                self.lw_sets.item(i).setCheckState(state)
            self.lw_sets.blockSignals(False)

        self._refresh_accessibility_after_check(self.lw_sets, item)

    def on_sets_select(self):
        checked = self.listwidget_items_checked(self.lw_sets)
        if not checked:
            QMessageBox.warning(self, "Auswahl", "Bitte mindestens eine Liste auswählen.")
            return

        checked_files = [c for c in checked if c != "Alle Vokabeln"]
        if not checked_files:
            checked_files = self.engine.list_sets(self.course_name)

        if not checked_files:
            QMessageBox.warning(self, "Auswahl", "Keine CSV-Dateien gefunden.")
            return

        self.selected_csvs = checked_files
        self.lang_a, self.lang_b = self.engine.get_common_languages(self.course_name, self.selected_csvs)

        self.refresh_direction_screen()
        self.set_screen(self.screen_direction)
        QTimer.singleShot(0, lambda: (self.lw_direction.setCurrentRow(0), self.lw_direction.setFocus()))

    # ----------------------------
    # Screen 3: Richtung + Vorschau + Reset
    # ----------------------------
    def build_screen_direction(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        self.lbl_direction_title = QLabel("Richtung und Optionen")
        self.lw_direction = QListWidget()
        self.lw_direction.setAccessibleName("Richtung")

        self._dummy_direction = QLineEdit()
        self._dummy_direction.setText(" ")
        self._dummy_direction.setAccessibleName(" ")
        self._dummy_direction.setReadOnly(True)
        self._dummy_direction.setFocusPolicy(Qt.NoFocus)
        self._dummy_direction.setStyleSheet(
            "border: 0px; background: transparent; color: transparent; selection-background-color: transparent;"
        )

        row = QHBoxLayout()

        self.btn_start = QPushButton("Start")
        self.btn_start.setAccessibleName("Starten")
        self.btn_start.clicked.connect(self.on_start_learning_or_view)
        self.btn_start.setDefault(True)
        self.btn_start.setAutoDefault(True)

        self.btn_dir_back = QPushButton("Zurück")
        self.btn_dir_back.setAccessibleName("Zurück")
        self.btn_dir_back.clicked.connect(self.on_dir_back)

        row.addWidget(self.btn_start)
        row.addWidget(self.btn_dir_back)

        layout.addWidget(self.lbl_direction_title)
        layout.addWidget(self.lw_direction)
        layout.addWidget(self._dummy_direction)
        layout.addLayout(row)
        return w

    def on_dir_back(self):
        self.set_screen(self.screen_sets)
        QTimer.singleShot(0, self._focus_first_sets_checkbox)

    def refresh_direction_screen(self):
        try:
            self.lw_direction.itemChanged.disconnect()
        except Exception:
            pass

        self.lw_direction.blockSignals(True)
        self.lw_direction.clear()

        # 3 Lernrichtungen + 1 Vorschau = exklusive Gruppe
        self._text_a2b = f"{self.lang_a} → {self.lang_b}"
        self._text_b2a = f"{self.lang_b} → {self.lang_a}"
        self._text_mix = "Gemischt (zufällig)"
        self._text_view = "Vokabeln anschauen"

        it1 = QListWidgetItem(self._text_a2b)
        it1.setFlags(it1.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it1.setCheckState(Qt.Checked)

        it2 = QListWidgetItem(self._text_b2a)
        it2.setFlags(it2.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it2.setCheckState(Qt.Unchecked)

        it_mix = QListWidgetItem(self._text_mix)
        it_mix.setFlags(it_mix.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it_mix.setCheckState(Qt.Unchecked)

        it_view = QListWidgetItem(self._text_view)
        it_view.setFlags(it_view.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it_view.setCheckState(Qt.Unchecked)

        it_reset = QListWidgetItem("Lernstand zurücksetzen")
        it_reset.setFlags(it_reset.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it_reset.setCheckState(Qt.Unchecked)

        self.lw_direction.addItem(it1)
        self.lw_direction.addItem(it2)
        self.lw_direction.addItem(it_mix)
        self.lw_direction.addItem(it_view)
        self.lw_direction.addItem(it_reset)

        self.lw_direction.blockSignals(False)
        self.lw_direction.itemChanged.connect(self.on_direction_item_changed)
        self.lw_direction.setCurrentRow(0)

    def on_direction_item_changed(self, item: QListWidgetItem):
        # Exklusiv: A2B, B2A, MIX, VIEW
        exclusive = {self._text_a2b, self._text_b2a, self._text_mix, self._text_view}

        if item.text() in exclusive and item.checkState() == Qt.Checked:
            self.lw_direction.blockSignals(True)
            for i in range(self.lw_direction.count()):
                it = self.lw_direction.item(i)
                if it is item:
                    continue
                if it.text() in exclusive:
                    it.setCheckState(Qt.Unchecked)
            self.lw_direction.blockSignals(False)

        self._refresh_accessibility_after_check(self.lw_direction, item)

    def on_start_learning_or_view(self):
        if not self.course_name or not self.selected_csvs:
            QMessageBox.critical(self, "Fehler", "Kurs oder Vokabeldateien fehlen.")
            return

        # Reihenfolge: 0=A2B, 1=B2A, 2=MIX, 3=VIEW, 4=RESET
        dir_a2b = self.lw_direction.item(0).checkState() == Qt.Checked
        dir_b2a = self.lw_direction.item(1).checkState() == Qt.Checked
        dir_mix = self.lw_direction.item(2).checkState() == Qt.Checked
        dir_view = self.lw_direction.item(3).checkState() == Qt.Checked

        if sum([dir_a2b, dir_b2a, dir_mix, dir_view]) != 1:
            QMessageBox.warning(self, "Modus", "Bitte genau einen Modus wählen.")
            return

        self.reset_progress = self.lw_direction.item(4).checkState() == Qt.Checked

        if dir_view:
            # ✅ Vorschau öffnen (ohne Session)
            self.open_view_screen()
            return

        if dir_a2b:
            self.direction = "A2B"
        elif dir_b2a:
            self.direction = "B2A"
        else:
            self.direction = "MIX"

        try:
            self.session = self.engine.start_session(
                course_name=self.course_name,
                selected_csvs=self.selected_csvs,
                direction=self.direction,
                reset_progress=self.reset_progress
            )
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        self.set_screen(self.screen_learn)
        self.start_round()

    # ----------------------------
    # Screen VIEW: Vokabeln anschauen
    # ----------------------------
    def build_screen_view(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        self.lbl_view = QLabel("Vokabeln")
        self.lw_view = QListWidget()
        self.lw_view.setAccessibleName("Vokabelliste")

        self.btn_view_done = QPushButton("Fertig")
        self.btn_view_done.setAccessibleName("Fertig")
        self.btn_view_done.clicked.connect(self.on_view_done)
        self.btn_view_done.setDefault(True)
        self.btn_view_done.setAutoDefault(True)

        layout.addWidget(self.lbl_view)
        layout.addWidget(self.lw_view)
        layout.addWidget(self.btn_view_done)
        return w

    def open_view_screen(self):
        if not self.course_name or not self.selected_csvs:
            QMessageBox.warning(self, "Fehler", "Bitte zuerst Kurs und Vokabeldateien auswählen.")
            return

        self.lbl_view.setText(f"Vokabeln ({self.lang_a} | {self.lang_b})")

        self.lw_view.clear()
        try:
            pairs = self.engine.load_vocab_pairs(self.course_name, self.selected_csvs)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        if not pairs:
            self.lw_view.addItem(QListWidgetItem("Keine Vokabeln gefunden."))
        else:
            for a, b in pairs:
                self.lw_view.addItem(QListWidgetItem(f"{a}  —  {b}"))

        self.set_screen(self.screen_view)
        QTimer.singleShot(0, lambda: (self.lw_view.setCurrentRow(0), self.lw_view.setFocus()))

    def on_view_done(self):
        self.set_screen(self.screen_direction)
        QTimer.singleShot(0, lambda: (self.lw_direction.setFocus(), self.lw_direction.setCurrentRow(0)))

    # ----------------------------
    # Screen 4: Lernen
    # ----------------------------
    def build_screen_learn(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        self.lbl_prompt = QLabel("Wort:")
        self.inp_answer = QLineEdit()
        self.inp_answer.setAccessibleName("Eingabe")

        self.out = QListWidget()
        self.out.setAccessibleName("")

        btn_row = QHBoxLayout()
        self.btn_next = QPushButton("Weiter")
        self.btn_next.setAccessibleName("Weiter")
        self.btn_next.clicked.connect(self.on_next_clicked)
        self.btn_next.setEnabled(False)
        self.btn_next.setDefault(False)
        self.btn_next.setAutoDefault(True)
        btn_row.addWidget(self.btn_next)

        layout.addWidget(self.lbl_prompt)
        layout.addWidget(self.inp_answer)
        layout.addWidget(self.out)
        layout.addLayout(btn_row)

        self.inp_answer.returnPressed.connect(self.on_submit_answer)
        QShortcut(QKeySequence("Escape"), w, activated=self.inp_answer.clear)
        return w

    def start_round(self):
        assert self.session is not None
        self.out.clear()
        self._cancel_pending_focus_timer()

        self.btn_next.setEnabled(False)
        self.btn_next.setDefault(False)
        self.inp_answer.setEnabled(True)
        self.inp_answer.clear()

        self.ask_next_word(set_focus=True)

    def ask_next_word(self, set_focus: bool = True):
        assert self.session is not None

        entry = self.session.next_item()
        if entry is None:
            self._cancel_pending_focus_timer()
            self.session.persist()
            self.show_done_screen()
            return

        self.current_entry = entry
        prompt, solution = self.session.get_prompt_and_answer(entry)
        self.current_prompt = prompt
        self.current_solution = solution

        self.lbl_prompt.setText(f"{prompt}:")
        self.inp_answer.setAccessibleName(prompt)

        self.inp_answer.setEnabled(True)
        self.inp_answer.clear()

        self.btn_next.setEnabled(False)
        self.btn_next.setDefault(False)

        if set_focus:
            self.inp_answer.setFocus()

    def on_submit_answer(self):
        if self.session is None or self.current_entry is None:
            return

        user = self.inp_answer.text()

        try:
            correct, user_norm, sol_norm, _new_score = self.session.check_answer(user)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        self._cancel_pending_focus_timer()

        if self.session.is_finished():
            self.session.persist()
            self.show_done_screen()
            return

        if correct:
            self.out.setAccessibleName("Richtig")
            self.out.insertItem(0, QListWidgetItem("Richtig"))
            self.out.setCurrentRow(0)

            self.inp_answer.setEnabled(False)
            self.out.setFocus()
            QApplication.processEvents()

            self.ask_next_word(set_focus=False)

            t = QTimer(self)
            t.setSingleShot(True)

            def back_to_input():
                self._pending_focus_timer = None
                self.inp_answer.setEnabled(True)
                self.inp_answer.setFocus()

            t.timeout.connect(back_to_input)
            self._pending_focus_timer = t
            t.start(25)
        else:
            self.out.setAccessibleName("")
            line = f"Falsch: {self.current_prompt}, Du: {user_norm}, Lösung: {sol_norm}"
            self.out.insertItem(0, QListWidgetItem(line))
            self.out.setCurrentRow(0)

            self.inp_answer.setEnabled(False)
            self.btn_next.setEnabled(True)
            self.btn_next.setDefault(True)
            self.out.setFocus()

    def on_next_clicked(self):
        self._cancel_pending_focus_timer()
        self.ask_next_word(set_focus=True)

    # ----------------------------
    # Screen 5: Done
    # ----------------------------
    def build_screen_done(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout()
        w.setLayout(layout)

        self.lbl_done = QLabel("")
        layout.addWidget(self.lbl_done)

        self.btn_continue = QPushButton("Weiter")
        self.btn_continue.setAccessibleName("Weiter")
        self.btn_continue.clicked.connect(self.on_continue_next_level)
        self.btn_continue.setDefault(True)
        self.btn_continue.setAutoDefault(True)

        self.btn_continue_reverse = QPushButton("Mit umgekehrter Fragerichtung weiterlernen")
        self.btn_continue_reverse.setAccessibleName("Mit umgekehrter Fragerichtung weiterlernen")
        self.btn_continue_reverse.clicked.connect(self.on_continue_reverse_direction)
        self.btn_continue_reverse.setDefault(False)
        self.btn_continue_reverse.setAutoDefault(False)

        self.btn_continue_mix = QPushButton("Mit gemischter Abfrage weiterlernen")
        self.btn_continue_mix.setAccessibleName("Mit gemischter Abfrage weiterlernen")
        self.btn_continue_mix.clicked.connect(self.on_continue_mixed_direction)
        self.btn_continue_mix.setDefault(False)
        self.btn_continue_mix.setAutoDefault(False)

        self.btn_done_exit = QPushButton("Beenden")
        self.btn_done_exit.setAccessibleName("Beenden")
        self.btn_done_exit.clicked.connect(self.on_done_back_to_sets)
        self.btn_done_exit.setDefault(False)
        self.btn_done_exit.setAutoDefault(False)

        self.btn_continue.installEventFilter(self)
        self.btn_continue_reverse.installEventFilter(self)
        self.btn_continue_mix.installEventFilter(self)
        self.btn_done_exit.installEventFilter(self)

        layout.addWidget(self.btn_continue)
        layout.addWidget(self.btn_continue_reverse)
        layout.addWidget(self.btn_continue_mix)
        layout.addWidget(self.btn_done_exit)
        return w

    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            for b in (
                getattr(self, "btn_continue", None),
                getattr(self, "btn_continue_reverse", None),
                getattr(self, "btn_continue_mix", None),
                getattr(self, "btn_done_exit", None),
            ):
                if isinstance(b, QPushButton):
                    b.setDefault(False)

            if obj is self.btn_continue:
                self.btn_continue.setDefault(True)
            elif obj is self.btn_continue_reverse:
                self.btn_continue_reverse.setDefault(True)
            elif obj is self.btn_continue_mix:
                self.btn_continue_mix.setDefault(True)
            elif obj is self.btn_done_exit:
                self.btn_done_exit.setDefault(True)

        return super().eventFilter(obj, event)

    def _get_done_level(self) -> int:
        step = self.engine.level_step if self.engine.level_step > 0 else 1
        level = 0
        if self.session and self.session.file_states:
            target_score = self.session.file_states[0].target_score
            level = target_score // step
        return level

    def show_done_screen(self):
        level = self._get_done_level()
        self.lbl_done.setText(f"Du hast Level {level} erreicht.")
        self.btn_continue.setText(f"Du hast Level {level} erreicht – weiter mit nächstem Level?")
        self.btn_continue.setAccessibleName(f"Du hast Level {level} erreicht weiter mit nächstem Level")

        self.btn_continue.setDefault(True)
        self.set_screen(self.screen_done)
        self.btn_continue.setFocus()

    def _start_session_with_direction(self, direction: str):
        if not self.course_name or not self.selected_csvs:
            return
        try:
            self.direction = direction
            self.session = self.engine.start_session(
                course_name=self.course_name,
                selected_csvs=self.selected_csvs,
                direction=self.direction,
                reset_progress=False
            )
        except Exception as e:
            QMessageBox.critical(self, "Fehler", str(e))
            return

        self.set_screen(self.screen_learn)
        self.start_round()

    def on_continue_next_level(self):
        self._start_session_with_direction(self.direction)

    def on_continue_reverse_direction(self):
        if self.direction == "A2B":
            new_dir = "B2A"
        elif self.direction == "B2A":
            new_dir = "A2B"
        else:
            new_dir = "B2A"
        self._start_session_with_direction(new_dir)

    def on_continue_mixed_direction(self):
        self._start_session_with_direction("MIX")

    def on_done_back_to_sets(self):
        self._cancel_pending_focus_timer()
        self._cancel_bounce_timer()

        if self.session is not None:
            try:
                self.session.persist()
            except Exception:
                pass

        self.refresh_sets_list()
        self._clear_sets_selection()

        self.set_screen(self.screen_sets)
        QTimer.singleShot(0, self._focus_first_sets_checkbox)

    # ----------------------------
    # App Quit
    # ----------------------------
    def on_quit_app(self):
        self._cancel_pending_focus_timer()
        self._cancel_bounce_timer()
        if self.session is not None:
            try:
                self.session.persist()
            except Exception:
                pass
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(780, 600)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("Fehler passiert. Enter drücken zum Schließen...")
