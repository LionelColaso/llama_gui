"""The "move existing data?" dialog.

The dialog is the only place the user is asked what should happen to the data
behind a changed path, so the important behaviour is what it reports back: both
trees offered independently, a blocked tree never ticked, the three outcomes
(move / copy / leave the files alone) distinguishable, and notes (e.g. stranded
interrupted downloads) actually reaching the user.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel, QPushButton
from pytestqt.qtbot import QtBot

from app.gui.dialogs.relocate import RelocateDialog
from app.schemas import RelocationData, RelocationItem


def _backends(**overrides: object) -> RelocationItem:
    data: dict[str, object] = {
        "label": "Backends",
        "source": "C:/old/managed",
        "destination": "C:/new/managed",
        "files": 12,
        "total_bytes": 340 * 1024 * 1024,
    }
    data.update(overrides)
    return RelocationItem(**data)  # type: ignore[arg-type]


def _models(**overrides: object) -> RelocationItem:
    data: dict[str, object] = {
        "label": "Models",
        "source": "C:/old/models",
        "destination": "C:/new/models",
        "files": 3,
        "total_bytes": 4 * 1024 * 1024 * 1024,
    }
    data.update(overrides)
    return RelocationItem(**data)  # type: ignore[arg-type]


def _checks(dialog: RelocateDialog) -> dict[str, QCheckBox]:
    return dialog._checks


def _button(dialog: RelocateDialog, text: str) -> QPushButton:
    for button in dialog.findChildren(QPushButton):
        if button.text() == text:
            return button
    raise AssertionError(f"no {text!r} button in the dialog")


class TestRelocateDialog:
    def test_offers_both_trees_ticked(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends(), models=_models()))
        qtbot.addWidget(dialog)

        assert dialog.move_backends() is True
        assert dialog.move_models() is True
        assert dialog.moves_anything is True

    def test_the_three_actions_are_offered(self, qtbot: QtBot) -> None:
        """Move, copy and "paths only" must all be on offer, plus Cancel."""
        dialog = RelocateDialog(RelocationData(backends=_backends()))
        qtbot.addWidget(dialog)

        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        assert "Move & save" in labels
        assert "Copy & save" in labels
        assert "Save paths only" in labels
        assert any("Cancel" in label for label in labels)
        assert "Save without moving" not in labels

    @pytest.mark.parametrize(
        ("label", "choice"),
        [
            ("Move & save", RelocateDialog.MOVE),
            ("Copy & save", RelocateDialog.COPY),
            ("Save paths only", RelocateDialog.SAVE_PATHS_ONLY),
        ],
    )
    def test_clicking_an_action_reports_it(
        self, qtbot: QtBot, label: str, choice: str
    ) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends()))
        qtbot.addWidget(dialog)

        _button(dialog, label).click()

        assert dialog.choice == choice
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_cancelling_rejects(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends()))
        qtbot.addWidget(dialog)

        _button(dialog, "Cancel").click()

        assert dialog.result() == QDialog.DialogCode.Rejected

    def test_the_choice_defaults_to_leaving_the_files_alone(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends()))
        qtbot.addWidget(dialog)

        assert dialog.choice == RelocateDialog.SAVE_PATHS_ONLY

    def test_shows_both_source_and_destination(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends()))
        qtbot.addWidget(dialog)

        text = " ".join(
            label.text() for label in dialog.findChildren(QLabel) if label.text()
        )
        assert "C:/old/managed" in text
        assert "C:/new/managed" in text

    def test_a_blocked_tree_is_never_ticked(self, qtbot: QtBot) -> None:
        """Offering an impossible transfer as pre-selected would fail the save."""
        dialog = RelocateDialog(
            RelocationData(
                backends=_backends(blocked="the new location already contains files"),
                models=_models(),
            )
        )
        qtbot.addWidget(dialog)

        assert dialog.move_backends() is False
        assert dialog.move_models() is True
        assert dialog.moves_anything is True

    def test_an_unticked_tree_is_not_transferred(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData(backends=_backends(), models=_models()))
        qtbot.addWidget(dialog)
        _checks(dialog)["Models"].setChecked(False)

        assert dialog.move_backends() is True
        assert dialog.move_models() is False

    def test_notes_reach_the_user(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(
            RelocationData(
                backends=_backends(),
                notes=["2 interrupted model download(s) stay behind."],
            )
        )
        qtbot.addWidget(dialog)

        text = " ".join(
            label.text() for label in dialog.findChildren(QLabel) if label.text()
        )
        assert "interrupted model download" in text

    def test_a_plan_with_nothing_to_move_transfers_nothing(self, qtbot: QtBot) -> None:
        dialog = RelocateDialog(RelocationData())
        qtbot.addWidget(dialog)

        assert dialog.moves_anything is False
        assert _checks(dialog) == {}
