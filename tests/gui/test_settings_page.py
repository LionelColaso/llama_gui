"""The Settings and Server options pages: collecting and persisting values."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton
from pytestqt.qtbot import QtBot

from app.gui.dialogs.relocate import RelocateDialog
from app.gui.pages.server_args import ServerArgsPage, _PathEdit
from app.gui.pages.settings import SettingsPage
from app.schemas import RelocationData, RelocationItem


def _set_row_value(page: ServerArgsPage, flag: str, value: str) -> None:
    """Set ``value`` on the editor row for ``flag``, whatever its widget kind."""
    for arg, editor in page._rows:
        if arg.flag != flag:
            continue
        if isinstance(editor, QComboBox):
            index = editor.findText(value)
            if index >= 0:
                editor.setCurrentIndex(index)
        elif isinstance(editor, _PathEdit):
            editor.setValue(value)
        elif isinstance(editor, QLineEdit):
            editor.setText(value)
        return


class TestSettingsPage:
    def test_creates(self, qtbot: QtBot, fake_orch: MagicMock) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        assert page._root_picker is not None

    def test_collect_round_trips_os_toggle(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        page._os_llama_check.setChecked(True)

        collected = page.collect()
        assert collected["use_os_llama_server"] is True
        # The legacy pointed-path model is gone from the settings form.
        assert "pointed" not in collected
        assert "source_priority" not in collected

    def test_save_persists_through_the_orchestrator(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        page._port_spin.setValue(9099)
        page._save()

        saved = fake_orch.save_config.call_args.args[0]
        assert saved["port"] == 9099
        # Unrelated settings travel with the save so nothing is dropped.
        assert "use_os_llama_server" in saved
        assert "pointed" not in saved


class TestUseDefaultRoot:
    @staticmethod
    def _button(page: SettingsPage) -> QPushButton:
        """The "Use Default" action button on the Managed root row."""
        button = page._root_picker._action_btn
        assert button is not None, "the root row has no action button"
        return button

    def test_the_root_row_offers_a_use_default_action(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        assert self._button(page).text() == "Use Default"

    def test_clicking_it_resets_the_root_and_reports_the_new_one(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        from app.schemas import ConfigData

        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.reset_root.return_value = ConfigData(
            values={},
            warnings=[
                "Managed root reset to C:/default (was C:/old). Restart the app."
            ],
        )
        fake_orch.cfg.root = "C:/default"

        self._button(page).click()

        fake_orch.reset_root.assert_called_once()
        # The button goes through reset_root, not save_config: a save would
        # freeze today's default path into the file as an override.
        fake_orch.save_config.assert_not_called()
        assert page._root_picker.text() == "C:/default"
        assert "C:/default" in page._status_label.text()

    def test_a_defaulted_root_shows_only_the_hint_and_stays_blank(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """A defaulted root must not be pre-filled.

        ``collect()`` sends whatever the field holds, so a filled field would be
        saved back as an override on the next unrelated settings save.
        """
        from app.paths import default_root

        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.cfg.root = str(default_root())
        fake_orch.cfg.root_is_default = True
        fake_orch.cfg.models_dir = ""
        fake_orch.cfg.models_dir_is_default = True

        page._load()

        assert page._root_picker.text() == ""
        assert page._models_dir_picker.text() == ""
        assert str(default_root()) in page._root_picker._edit.placeholderText()
        assert page.collect()["root"] == ""
        assert page.collect()["models_dir"] == ""

    def test_a_failing_reset_is_reported_not_raised(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.reset_root.side_effect = OSError("read-only volume")

        self._button(page).click()

        assert "read-only volume" in page._status_label.text()


class TestUseDefaultModelsDir:
    @staticmethod
    def _button(page: SettingsPage) -> QPushButton:
        button = page._models_dir_picker._action_btn
        assert button is not None, "the models directory row has no action button"
        return button

    def test_the_models_row_offers_a_use_default_action(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        assert self._button(page).text() == "Use Default"

    def test_clicking_it_resets_the_models_directory(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        from app.schemas import ConfigData

        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.cfg.models_dir = "C:/somewhere/else"
        fake_orch.cfg.models_dir_is_default = False
        page._load()
        assert page._models_dir_picker.text() == "C:/somewhere/else"

        fake_orch.reset_models_dir.return_value = ConfigData(
            values={},
            warnings=[
                (
                    "Models directory reset to C:/default/models (was "
                    "C:/somewhere/else). Restart the app."
                )
            ],
        )
        fake_orch.cfg.models_dir = ""
        fake_orch.cfg.models_dir_is_default = True

        self._button(page).click()

        fake_orch.reset_models_dir.assert_called_once()
        fake_orch.save_config.assert_not_called()
        assert page._models_dir_picker.text() == ""
        assert "C:/default/models" in page._status_label.text()

    def test_a_default_models_dir_is_shown_as_a_hint_only(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)

        # The fixture config has no models_dir override.
        assert fake_orch.cfg.models_dir == ""
        assert page._models_dir_picker.text() == ""
        assert str(fake_orch.cfg.models_dir_path) in (
            page._models_dir_picker._edit.placeholderText()
        )

    def test_a_failing_reset_is_reported_not_raised(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.reset_models_dir.side_effect = OSError("read-only volume")

        self._button(page).click()

        assert "read-only volume" in page._status_label.text()


def _plan() -> RelocationData:
    """A plan offering both trees, so tests can tick one at a time."""
    return RelocationData(
        backends=RelocationItem(
            label="Backends",
            source="C:/old/managed",
            destination="C:/new/managed",
            files=2,
            total_bytes=2048,
        ),
        models=RelocationItem(
            label="Models",
            source="C:/old/models",
            destination="C:/new/models",
            files=1,
            total_bytes=1024,
        ),
    )


class TestRelocationOnSave:
    """Changing a path offers to move or copy the data that is already there."""

    @staticmethod
    def _page(qtbot: QtBot, orch: MagicMock) -> SettingsPage:
        page = SettingsPage(orch)
        qtbot.addWidget(page)
        page._root_picker.setText("C:/new")
        page._models_dir_picker.setText("C:/new/models")
        return page

    @staticmethod
    def _dialog(dialog_cls: MagicMock, choice: str) -> MagicMock:
        """A dialog whose buttons were set to ``choice``."""
        # The page compares against the dialog class constants, so the patched
        # class has to expose the real ones.
        dialog_cls.MOVE = RelocateDialog.MOVE
        dialog_cls.COPY = RelocateDialog.COPY
        dialog_cls.SAVE_PATHS_ONLY = RelocateDialog.SAVE_PATHS_ONLY
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.choice = choice
        dialog.moves_anything = True
        dialog.move_backends.return_value = True
        dialog.move_models.return_value = True
        return dialog

    def test_an_unchanged_path_never_asks(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = SettingsPage(fake_orch)
        qtbot.addWidget(page)
        fake_orch.plan_relocation.return_value = RelocationData()

        with (
            patch("app.gui.pages.settings.RelocateDialog") as dialog_cls,
            patch.object(fake_orch, "relocate_data") as relocate,
        ):
            page._save()

        dialog_cls.assert_not_called()
        relocate.assert_not_called()
        fake_orch.save_config.assert_called_once()

    def test_choosing_to_move_runs_the_worker_and_then_saves(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()
        fake_orch.relocate_data.return_value = RelocationData(
            backends=RelocationItem(label="Backends", transfer="move", files=2)
        )

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog_cls.return_value = self._dialog(dialog_cls, RelocateDialog.MOVE)
            page._save()

        kwargs = fake_orch.relocate_data.call_args.kwargs
        assert kwargs["root"] == "C:/new"
        assert kwargs["models_dir"] == "C:/new/models"
        assert kwargs["transfer"] == "move"
        assert kwargs["move_backends"] is True
        assert kwargs["move_models"] is True
        # The settings are saved only after the move reported back.
        assert fake_orch.save_config.call_count == 1
        assert "Moved Backends" in page._status_label.text()

    def test_choosing_copy_keeps_the_originals(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()
        fake_orch.relocate_data.return_value = RelocationData(
            models=RelocationItem(label="Models", transfer="copy", files=1)
        )

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog_cls.return_value = self._dialog(dialog_cls, RelocateDialog.COPY)
            page._save()

        assert fake_orch.relocate_data.call_args.kwargs["transfer"] == "copy"
        assert "Copied Models" in page._status_label.text()
        assert fake_orch.save_config.call_count == 1

    def test_choosing_save_paths_only_skips_the_transfer(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog_cls.return_value = self._dialog(
                dialog_cls, RelocateDialog.SAVE_PATHS_ONLY
            )
            page._save()

        fake_orch.relocate_data.assert_not_called()
        fake_orch.save_config.assert_called_once()

    def test_unticking_everything_skips_the_transfer(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """Move & save with nothing ticked is a plain save."""
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog = self._dialog(dialog_cls, RelocateDialog.MOVE)
            dialog.moves_anything = False
            dialog_cls.return_value = dialog
            page._save()

        fake_orch.relocate_data.assert_not_called()
        fake_orch.save_config.assert_called_once()

    def test_cancelling_changes_nothing(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog_cls.return_value.exec.return_value = QDialog.DialogCode.Rejected
            page._save()

        fake_orch.relocate_data.assert_not_called()
        fake_orch.save_config.assert_not_called()
        assert "nothing was changed" in page._status_label.text()

    def test_only_the_ticked_trees_are_transferred(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """Unticking one box transfers only the other one."""
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog = self._dialog(dialog_cls, RelocateDialog.MOVE)
            dialog.move_models.return_value = False
            dialog_cls.return_value = dialog
            page._save()

        kwargs = fake_orch.relocate_data.call_args.kwargs
        assert kwargs["move_backends"] is True
        assert kwargs["move_models"] is False

    def test_a_failed_transfer_leaves_the_settings_alone(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        """The config must still point at the data that is still on disk."""
        page = self._page(qtbot, fake_orch)
        fake_orch.plan_relocation.return_value = _plan()
        fake_orch.relocate_data.side_effect = OSError("no space left on device")

        with patch("app.gui.pages.settings.RelocateDialog") as dialog_cls:
            dialog_cls.return_value = self._dialog(dialog_cls, RelocateDialog.MOVE)
            page._save()

        fake_orch.save_config.assert_not_called()
        assert "no space left on device" in page._status_label.text()
        assert "not changed" in page._status_label.text()


class TestServerArgsPage:
    def test_collect_includes_dedicated_and_extra_args(
        self, qtbot: QtBot, fake_orch: MagicMock
    ) -> None:
        page = ServerArgsPage(fake_orch)
        qtbot.addWidget(page)

        _set_row_value(page, "--ctx-size", "8192")
        _set_row_value(page, "--n-gpu-layers", "33")
        _set_row_value(page, "__extra_args__", "--threads 8")

        collected = page.collect()
        # ServerArgsPage returns dedicated fields separately
        assert collected["ctx_size"] == 8192
        assert collected["n_gpu_layers"] == 33
        assert collected["extra_server_args"] == "--threads 8"
        assert "listen_flag" not in collected
