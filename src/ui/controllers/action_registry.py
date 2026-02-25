"""Central QAction/QMenu construction for the main window."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenuBar

from src.core.keybindings import get_action_sequence, normalize_keybindings, qkeysequence_from_sequence


class ActionRegistry:
    @staticmethod
    def _register_shortcut_action(
        ide: Any,
        action: QAction,
        *,
        scope: str,
        action_ids: tuple[str, ...],
    ) -> None:
        specs = getattr(ide, "_shortcut_action_specs", None)
        if not isinstance(specs, list):
            specs = []
            ide._shortcut_action_specs = specs
        specs.append(
            {
                "action": action,
                "scope": str(scope or "general"),
                "action_ids": tuple(str(item or "").strip() for item in action_ids if str(item or "").strip()),
            }
        )

    @staticmethod
    def apply_keybindings(main_window: Any) -> None:
        ide = main_window
        specs = getattr(ide, "_shortcut_action_specs", [])
        if not isinstance(specs, list) or not specs:
            return
        try:
            keybindings = normalize_keybindings(
                ide.settings_manager.get("keybindings", scope_preference="ide", default={})
            )
        except Exception:
            keybindings = normalize_keybindings({})

        for spec in specs:
            action = spec.get("action")
            if not isinstance(action, QAction):
                continue
            scope = str(spec.get("scope") or "general")
            action_ids = tuple(spec.get("action_ids") or ())

            seen_texts: set[str] = set()
            sequences = []
            for action_id in action_ids:
                seq = get_action_sequence(keybindings, scope=scope, action_id=action_id)
                qseq = qkeysequence_from_sequence(seq)
                text = qseq.toString()
                if not text or text in seen_texts:
                    continue
                seen_texts.add(text)
                sequences.append(qseq)

            if not sequences:
                action.setShortcut("")
                continue
            if len(sequences) == 1:
                action.setShortcut(sequences[0])
                continue
            action.setShortcuts(sequences)

    @staticmethod
    def create_actions(main_window) -> None:
        ide = main_window
        ide._shortcut_action_specs = []

        menubar = QMenuBar(ide)
        menubar.setNativeMenuBar(False)
        ide.add_window_left_control(menubar)

        file_menu = menubar.addMenu("&File")

        act_new = QAction("New File", ide)
        act_new.triggered.connect(ide.new_file)
        ActionRegistry._register_shortcut_action(ide, act_new, scope="general", action_ids=("action.new_file",))
        file_menu.addAction(act_new)

        act_open = QAction("Open File...", ide)
        act_open.triggered.connect(ide.open_file_dialog)
        ActionRegistry._register_shortcut_action(ide, act_open, scope="general", action_ids=("action.open_file",))
        file_menu.addAction(act_open)

        act_open_project = QAction("Open Project...", ide)
        act_open_project.triggered.connect(ide.open_project_dialog)
        ActionRegistry._register_shortcut_action(ide, act_open_project, scope="general", action_ids=("action.open_project",))
        file_menu.addAction(act_open_project)

        act_new_project = QAction("New Project...", ide)
        act_new_project.triggered.connect(ide.open_new_project_dialog)
        ActionRegistry._register_shortcut_action(ide, act_new_project, scope="general", action_ids=("action.new_project",))
        file_menu.addAction(act_new_project)

        ide.recent_projects_menu = file_menu.addMenu("Recent Projects")
        ide._refresh_recent_projects_menu()

        act_find_in_files = QAction("Find in Files...", ide)
        act_find_in_files.triggered.connect(ide.open_find_in_files_dialog)
        ActionRegistry._register_shortcut_action(
            ide,
            act_find_in_files,
            scope="general",
            action_ids=("action.find_in_files",),
        )
        file_menu.addAction(act_find_in_files)

        file_menu.addSeparator()

        act_save = QAction("Save", ide)
        act_save.triggered.connect(ide.save_current_editor)
        ActionRegistry._register_shortcut_action(ide, act_save, scope="general", action_ids=("action.save",))
        file_menu.addAction(act_save)

        act_save_as = QAction("Save As...", ide)
        act_save_as.triggered.connect(ide.save_current_editor_as)
        ActionRegistry._register_shortcut_action(ide, act_save_as, scope="general", action_ids=("action.save_as",))
        file_menu.addAction(act_save_as)

        file_menu.addSeparator()

        act_close_editor = QAction("Close Active Editor", ide)
        act_close_editor.triggered.connect(ide.close_active_editor)
        ActionRegistry._register_shortcut_action(ide, act_close_editor, scope="general", action_ids=("action.close_editor",))
        file_menu.addAction(act_close_editor)

        act_close_project = QAction("Close Project", ide)
        act_close_project.triggered.connect(ide.close_project)
        file_menu.addAction(act_close_project)
        ide._act_close_project = act_close_project

        file_menu.addSeparator()

        act_settings = QAction("Settings...", ide)
        act_settings.triggered.connect(ide.open_settings)
        file_menu.addAction(act_settings)

        act_exit = QAction("Exit", ide)
        act_exit.triggered.connect(ide.close)
        ActionRegistry._register_shortcut_action(ide, act_exit, scope="general", action_ids=("action.exit",))
        file_menu.addAction(act_exit)

        edit_menu = menubar.addMenu("&Edit")

        act_copy = QAction("Copy", ide)
        act_copy.triggered.connect(ide.copy_focused_widget)
        ActionRegistry._register_shortcut_action(ide, act_copy, scope="general", action_ids=("action.copy",))
        edit_menu.addAction(act_copy)

        act_paste = QAction("Paste", ide)
        act_paste.triggered.connect(ide.paste_into_focused_widget)
        ActionRegistry._register_shortcut_action(ide, act_paste, scope="general", action_ids=("action.paste",))
        edit_menu.addAction(act_paste)

        edit_menu.addSeparator()

        act_find = QAction("Find", ide)
        act_find.triggered.connect(ide.show_find_in_editor)
        ActionRegistry._register_shortcut_action(ide, act_find, scope="general", action_ids=("action.find",))
        edit_menu.addAction(act_find)

        act_replace = QAction("Replace", ide)
        act_replace.triggered.connect(ide.show_replace_in_editor)
        ActionRegistry._register_shortcut_action(ide, act_replace, scope="general", action_ids=("action.replace",))
        edit_menu.addAction(act_replace)

        edit_menu.addSeparator()

        act_format_file = QAction("Format File", ide)
        act_format_file.triggered.connect(ide.format_current_file)
        edit_menu.addAction(act_format_file)
        ide._act_format_file = act_format_file

        act_format_selection = QAction("Format Selection", ide)
        act_format_selection.triggered.connect(ide.format_current_selection)
        edit_menu.addAction(act_format_selection)
        ide._act_format_selection = act_format_selection

        edit_menu.addSeparator()

        act_go_def = QAction("Go to Definition", ide)
        act_go_def.triggered.connect(ide.go_to_definition)
        ActionRegistry._register_shortcut_action(
            ide,
            act_go_def,
            scope="general",
            action_ids=("action.go_to_definition",),
        )
        edit_menu.addAction(act_go_def)

        act_find_usages = QAction("Find Usages", ide)
        act_find_usages.triggered.connect(ide.find_usages)
        ActionRegistry._register_shortcut_action(
            ide,
            act_find_usages,
            scope="general",
            action_ids=("action.find_usages",),
        )
        edit_menu.addAction(act_find_usages)

        act_rename_symbol = QAction("Rename Symbol...", ide)
        act_rename_symbol.triggered.connect(ide.rename_symbol)
        ActionRegistry._register_shortcut_action(
            ide,
            act_rename_symbol,
            scope="general",
            action_ids=("action.rename_symbol",),
        )
        edit_menu.addAction(act_rename_symbol)
        ide._act_rename_symbol = act_rename_symbol

        act_extract_variable = QAction("Extract Variable...", ide)
        act_extract_variable.triggered.connect(ide.extract_variable)
        ActionRegistry._register_shortcut_action(
            ide,
            act_extract_variable,
            scope="general",
            action_ids=("action.extract_variable",),
        )
        edit_menu.addAction(act_extract_variable)
        ide._act_extract_variable = act_extract_variable

        act_extract_method = QAction("Extract Method...", ide)
        act_extract_method.triggered.connect(ide.extract_method)
        ActionRegistry._register_shortcut_action(
            ide,
            act_extract_method,
            scope="general",
            action_ids=("action.extract_method",),
        )
        edit_menu.addAction(act_extract_method)
        ide._act_extract_method = act_extract_method

        view = menubar.addMenu("&View")
        ide._panel_toggle_actions = []

        act_panel_project = ide.dock_project.toggleViewAction()
        act_panel_project.setText("Project Dock")
        ide._panel_toggle_actions.append(act_panel_project)
        view.addAction(act_panel_project)

        act_panel_debug = ide.dock_debug.toggleViewAction()
        act_panel_debug.setText("Debug Dock")
        ide._panel_toggle_actions.append(act_panel_debug)
        view.addAction(act_panel_debug)

        act_panel_terminal = ide.dock_terminal.toggleViewAction()
        act_panel_terminal.setText("Terminal Dock")
        ide._panel_toggle_actions.append(act_panel_terminal)
        view.addAction(act_panel_terminal)

        if ide.dock_problems is not None:
            act_panel_problems = ide.dock_problems.toggleViewAction()
            act_panel_problems.setText("Problems Dock")
            ide._panel_toggle_actions.append(act_panel_problems)
            view.addAction(act_panel_problems)

        if ide.dock_usages is not None:
            act_panel_usages = ide.dock_usages.toggleViewAction()
            act_panel_usages.setText("Usages Dock")
            ide._panel_toggle_actions.append(act_panel_usages)
            view.addAction(act_panel_usages)

        if ide.dock_outline is not None:
            act_panel_outline = ide.dock_outline.toggleViewAction()
            act_panel_outline.setText("Outline Dock")
            ide._panel_toggle_actions.append(act_panel_outline)
            view.addAction(act_panel_outline)

        view_editor = view.addMenu("Editor")

        act_split_right = QAction("Split Right", ide)
        act_split_right.triggered.connect(ide.split_editor_right)
        view_editor.addAction(act_split_right)

        act_split_down = QAction("Split Down", ide)
        act_split_down.triggered.connect(ide.split_editor_down)
        view_editor.addAction(act_split_down)

        act_clear_diag = QAction("Clear Diagnostics", ide)
        act_clear_diag.triggered.connect(ide.clear_diagnostics)
        view_editor.addAction(act_clear_diag)

        git_menu = menubar.addMenu("&Git")
        act_clone_repo = QAction("Clone Repository...", ide)
        act_clone_repo.triggered.connect(lambda _checked=False: ide.open_clone_repository_dialog())
        git_menu.addAction(act_clone_repo)
        act_share_repo = QAction("Share to GitHub...", ide)
        act_share_repo.triggered.connect(lambda _checked=False: ide.open_share_to_github_dialog())
        git_menu.addAction(act_share_repo)
        git_menu.addSeparator()

        act_commit = QAction("Commit...", ide)
        act_commit.triggered.connect(lambda _checked=False: ide.open_git_commit_dialog())
        git_menu.addAction(act_commit)

        act_push = QAction("Push...", ide)
        act_push.triggered.connect(lambda _checked=False: ide.push_current_branch())
        git_menu.addAction(act_push)

        act_fetch = QAction("Fetch", ide)
        act_fetch.triggered.connect(lambda _checked=False: ide.fetch_remote())
        git_menu.addAction(act_fetch)

        act_pull = QAction("Pull", ide)
        act_pull.triggered.connect(lambda _checked=False: ide.pull_current_branch())
        git_menu.addAction(act_pull)

        act_preflight = QAction("Preflight Check...", ide)
        act_preflight.triggered.connect(lambda _checked=False: ide.run_git_preflight_check())
        git_menu.addAction(act_preflight)

        act_commit_push = QAction("Commit and Push...", ide)
        act_commit_push.triggered.connect(lambda _checked=False: ide.open_git_commit_dialog(prefer_push_action=True))
        git_menu.addAction(act_commit_push)

        git_menu.addSeparator()
        act_branches = QAction("Branches...", ide)
        act_branches.triggered.connect(lambda _checked=False: ide.open_git_branches_dialog())
        git_menu.addAction(act_branches)

        rollback_menu = git_menu.addMenu("Rollback")
        act_discard_unstaged = QAction("Discard Unstaged Changes...", ide)
        act_discard_unstaged.triggered.connect(lambda _checked=False: ide.rollback_discard_unstaged())
        rollback_menu.addAction(act_discard_unstaged)

        act_hard_reset = QAction("Hard Reset to HEAD...", ide)
        act_hard_reset.triggered.connect(lambda _checked=False: ide.rollback_hard_reset_head())
        rollback_menu.addAction(act_hard_reset)

        git_menu.addSeparator()
        act_git_refresh = QAction("Refresh Git Status", ide)
        act_git_refresh.triggered.connect(lambda: ide.schedule_git_status_refresh(delay_ms=0, force=True))
        git_menu.addAction(act_git_refresh)

        tools = menubar.addMenu("&Tools")
        act_complete = QAction("Trigger Completion", ide)
        act_complete.triggered.connect(ide.trigger_completion)
        ActionRegistry._register_shortcut_action(
            ide,
            act_complete,
            scope="general",
            action_ids=("action.trigger_completion",),
        )
        tools.addAction(act_complete)

        act_ai_inline = QAction("AI Inline Assist", ide)
        act_ai_inline.triggered.connect(ide.trigger_ai_inline_assist)
        ActionRegistry._register_shortcut_action(
            ide,
            act_ai_inline,
            scope="general",
            action_ids=(
                "action.ai_inline_assist",
                "action.ai_inline_assist_ctrl_alt_space",
                "action.ai_inline_assist_alt_space",
            ),
        )
        tools.addAction(act_ai_inline)

        act_new_terminal = QAction("New Terminal", ide)
        act_new_terminal.triggered.connect(ide.new_terminal_tab)
        ActionRegistry._register_shortcut_action(
            ide,
            act_new_terminal,
            scope="general",
            action_ids=("action.new_terminal",),
        )
        tools.addAction(act_new_terminal)
        ide._act_new_terminal = act_new_terminal

        run_menu = menubar.addMenu("&Run")
        run_cfg_menu = run_menu.addMenu("Run Configuration")
        run_cfg_menu.aboutToShow.connect(ide.populate_python_run_config_menu)
        ide._run_python_config_menu = run_cfg_menu
        cargo_cfg_menu = run_menu.addMenu("Cargo Configuration")
        cargo_cfg_menu.aboutToShow.connect(ide.populate_cargo_run_config_menu)
        ide._run_cargo_config_menu = cargo_cfg_menu
        build_cfg_menu = run_menu.addMenu("Build Configuration")
        build_cfg_menu.aboutToShow.connect(ide.populate_build_config_menu)
        ide._run_build_config_menu = build_cfg_menu

        act_build = QAction("Build Current File", ide)
        act_build.triggered.connect(ide.build_current_file)
        ActionRegistry._register_shortcut_action(
            ide,
            act_build,
            scope="general",
            action_ids=("action.build_current_file",),
        )
        run_menu.addAction(act_build)
        ide._act_build_current = act_build

        act_build_run = QAction("Build + Run Current File", ide)
        act_build_run.triggered.connect(ide.build_and_run_current_file)
        ActionRegistry._register_shortcut_action(
            ide,
            act_build_run,
            scope="general",
            action_ids=("action.build_and_run_current_file",),
        )
        run_menu.addAction(act_build_run)
        ide._act_build_and_run_current = act_build_run

        act_run = QAction("Run", ide)
        act_run.triggered.connect(ide.run_primary_python_target)
        ActionRegistry._register_shortcut_action(
            ide,
            act_run,
            scope="general",
            action_ids=("action.run_current_file",),
        )
        run_menu.addAction(act_run)
        ide._act_run_current = act_run

        act_rerun = QAction("Rerun Current File", ide)
        act_rerun.triggered.connect(ide.rerun_current_file)
        ActionRegistry._register_shortcut_action(
            ide,
            act_rerun,
            scope="general",
            action_ids=("action.rerun_current_file",),
        )
        run_menu.addAction(act_rerun)
        ide._act_rerun_current = act_rerun

        act_stop = QAction("Stop Current Run", ide)
        act_stop.triggered.connect(ide.stop_current_run)
        ActionRegistry._register_shortcut_action(
            ide,
            act_stop,
            scope="general",
            action_ids=("action.stop_current_run",),
        )
        run_menu.addAction(act_stop)
        ide._act_stop_current = act_stop

        help_menu = menubar.addMenu("&Help")

        act_help_docs = QAction("Documentation", ide)
        act_help_docs.triggered.connect(ide.open_documentation_viewer)
        help_menu.addAction(act_help_docs)

        help_menu.addSeparator()

        act_help_about = QAction(f"About {ide.APP_NAME}", ide)
        act_help_about.triggered.connect(ide.show_about_dialog)
        help_menu.addAction(act_help_about)

        ActionRegistry.apply_keybindings(ide)
        ide._setup_titlebar_toolbar_controls()
        ide._update_toolbar_run_controls()
