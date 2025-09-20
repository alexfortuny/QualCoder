# -*- coding: utf-8 -*-

"""
This file is part of QualCoder.

QualCoder is free software: you can redistribute it and/or modify it under the
terms of the GNU Lesser General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.

QualCoder is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Pu
blic License for more details.

You should have received a copy of the GNU Lesser General Public License along with QualCoder.
If not, see <https://www.gnu.org/licenses/>.

Author: Colin Curtain (ccbogel)
https://github.com/ccbogel/QualCoder
"""

import sqlite3
from copy import copy, deepcopy
import datetime
# import difflib  # Slow, kept this in case need to revert to it. Now using diff_match_patch
import diff_match_patch
import html
import logging
from operator import itemgetter
import os
import qtawesome as qta  # see: https://pictogrammers.com/library/mdi/
from random import randint
import re
import webbrowser

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor

from .add_item_name import DialogAddItemName
from .code_in_all_files import DialogCodeInAllFiles
from .color_selector import DialogColorSelect, colour_ranges, colors, TextColor, show_codes_of_colour_range
from .confirm_delete import DialogConfirmDelete
from .helpers import Message, DialogGetStartAndEndMarks, ExportDirectoryPathDialog, MarkdownHighlighter, NumberBar
from .GUI.ui_dialog_code_text import Ui_Dialog_code_text
from .memo import DialogMemo
from .report_attributes import DialogSelectAttributeParameters
from .reports import DialogReportCoderComparisons, DialogReportCodeFrequencies  # For isinstance()
from .report_codes import DialogReportCodes
from .report_code_summary import DialogReportCodeSummary  # For isinstance()
from .select_items import DialogSelectItems  # For isinstance()
from .ai_search_dialog import DialogAiSearch
from .ai_prompts import PromptsList, DialogAiEditPrompts
from .ai_chat import ai_chat_signal_emitter

ai_search_analysis_max_count = 10  # How many chunks of data are analysed in the second stage

path = os.path.abspath(os.path.dirname(__file__))
logger = logging.getLogger(__name__)


class DialogCodeText(QtWidgets.QWidget):
    """ Code management. Add, delete codes. Mark and unmark text.
    Add memos and colors to codes.
    Trialled using setHtml for documents, but on marking text Html formatting was replaced, also
    on unmarking text, the unmark was not immediately cleared (needed to reload the file). """

    NAME_COLUMN = 0
    ID_COLUMN = 1
    MEMO_COLUMN = 2
    app = None
    parent_textEdit = None
    tab_reports = None  # Tab widget reports, used for updates to codes
    codes = []
    recent_codes = []  # List of recent codes (up to 5) for textedit context menu
    categories = []
    tree_sort_option = "all asc"  # all desc, cat then code asc
    filenames = []
    file_ = None  # Contains filename and file id returned from SelectItems
    code_text = []
    annotations = []
    undo_deleted_codes = []

    # Overlapping coded text details
    overlaps_at_pos = []
    overlaps_at_pos_idx = 0

    # Search text variables
    search_type = "3"  # Three characters entered before search can begin
    search_indices = []
    search_index = 0
    search_term = ""
    selected_code_index = 0
    important = False  # Show/hide important codes
    attributes = []  # Show selected files using these attributes in list widget

    # Autocode variables
    all_first_last = "all"  # Autocode all instances or first or last in a file
    # A list of dictionaries of autocode history {title, list of dictionary of sql commands}
    autocode_history = []

    # Timers to reduce overly sensitive key events: overlap, re-size oversteps by multiple characters
    code_resize_timer = 0
    overlap_timer = 0
    text = ""

    # Variables for Edit mode, text above also
    ed_codetext = []
    ed_annotations = []
    ed_casetext = []
    prev_text = ""
    code_deletions = []
    edit_mode = False
    edit_pos = 0
    no_codes_annotes_cases = None
    edit_mode_has_changed = False
    # Revert to original if edit text caused problems
    edit_original_source_id = None
    edit_original_source = None
    edit_original_codes = None
    edit_original_annotations = None
    edit_original_case_assignment = None
    edit_original_cutoff_datetime = None

    # Variables associated with right-hand side splitter, for project memo, code rule
    project_memo = False
    code_rule = False

    # Variables for ai search
    ai_search_results = []
    ai_search_code_name = ''
    ai_search_code_memo = ''
    ai_search_file_ids = []
    ai_search_code_ids = []
    ai_search_similar_chunk_list = []
    ai_search_chunks_pos = 0
    ai_search_running = False
    ai_search_current_result_index = None
    ai_search_prompt = None
    ai_search_ai_model = None
    ai_include_coded_segments = None

    def __init__(self, app, parent_textedit, tab_reports):

        super(DialogCodeText, self).__init__()
        self.app = app
        self.tab_reports = tab_reports
        self.parent_textEdit = parent_textedit
        self.search_indices = []
        self.search_index = 0
        self.codes, self.categories = self.app.get_codes_categories()
        self.get_recent_codes()  # After codes obtained!
        self.tree_sort_option = "all asc"
        self.annotations = self.app.get_annotations()
        self.autocode_history = []
        self.undo_deleted_codes = []
        self.default_new_code_color = None
        self.project_memo = False
        self.code_rule = False
        self.important = False
        self.attributes = []
        self.code_resize_timer = datetime.datetime.now()
        self.overlap_timer = datetime.datetime.now()
        self.ui = Ui_Dialog_code_text()
        self.ui.setupUi(self)

        self.ui.groupBox_edit_mode.hide()
        ee = f'{_("EDITING TEXT MODE (Ctrl+E)")} '
        ee += _(
            "Avoid selecting sections of text with a combination of not underlined (not coded / annotated / "
            "case-assigned) and underlined (coded, annotated, case-assigned).")
        ee += " " + _(
            "Positions of the underlying codes / annotations / case-assigned may not correctly adjust if text is "
            "typed over or deleted.")
        self.ui.label_editing.setText(ee)
        self.edit_pos = 0
        self.edit_mode = False
        self.edit_original_source = None
        self.edit_original_source_id = None
        self.edit_original_codes = None
        self.edit_original_annotations = None
        self.edit_original_case_assignment = None
        self.edit_original_cutoff_datetime = None

        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        font = f'font: {self.app.settings["fontsize"]}pt "{self.app.settings["font"]}";'
        self.setStyleSheet(font)
        tree_font = f'font: {self.app.settings["treefontsize"]}pt "{self.app.settings["font"]}";'
        self.ui.treeWidget.setStyleSheet(tree_font)
        doc_font = f'font: {self.app.settings["docfontsize"]}pt "{self.app.settings["font"]}";'
        self.ui.textEdit.setStyleSheet(doc_font)
        self.ui.label_coder.setText(f"Coder: {self.app.settings['codername']}")
        self.ui.textEdit.setPlainText("")
        self.ui.textEdit.setAutoFillBackground(True)
        self.ui.textEdit.setToolTip("")
        self.ui.textEdit.setMouseTracking(True)
        self.ui.textEdit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | 
            Qt.TextInteractionFlag.TextSelectableByKeyboard  
        )        
        self.ui.textEdit.installEventFilter(self)
        self.eventFilterTT = ToolTipEventFilter()
        self.ui.textEdit.installEventFilter(self.eventFilterTT)
        self.ui.textEdit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.textEdit.customContextMenuRequested.connect(self.text_edit_menu)
        self.ui.textEdit.cursorPositionChanged.connect(self.overlapping_codes_in_text)
        self.ui.textEdit_info.setReadOnly(True)
        self.ui.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.listWidget.customContextMenuRequested.connect(self.file_menu)
        self.ui.listWidget.setStyleSheet(tree_font)
        self.ui.listWidget.selectionModel().selectionChanged.connect(self.file_selection_changed)
        self.search_type = "3"  # 3 character threshold for text search
        self.ui.lineEdit_search.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.lineEdit_search.customContextMenuRequested.connect(self.lineedit_search_menu)
        self.ui.lineEdit_search.returnPressed.connect(self.search_for_text)
        self.ui.tabWidget.currentChanged.connect(self.tab_changed)
        self.ui.tabWidget.setCurrentIndex(0)  # Defaults to list of documents
        self.get_files()

        # Buttons under files list
        self.ui.pushButton_latest.setIcon(qta.icon('mdi6.arrow-collapse-right'))
        self.ui.pushButton_latest.pressed.connect(self.go_to_latest_coded_file)
        self.ui.pushButton_next_file.setIcon(qta.icon('mdi6.arrow-right'))
        self.ui.pushButton_next_file.pressed.connect(self.go_to_next_file)
        self.ui.pushButton_bookmark_go.setIcon(qta.icon('mdi6.bookmark'))
        self.ui.pushButton_bookmark_go.pressed.connect(self.go_to_bookmark)
        self.ui.pushButton_document_memo.setIcon(qta.icon('mdi6.text-long'))
        self.ui.pushButton_document_memo.pressed.connect(self.active_file_memo)
        self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_file_attributes.pressed.connect(self.get_files_from_attributes)
        # Buttons under codes tree
        self.ui.pushButton_find_code.setIcon(qta.icon('mdi6.card-search-outline', options=[{'scale-factor': 1.2}]))
        self.ui.pushButton_find_code.pressed.connect(self.find_code_in_tree)
        self.ui.pushButton_show_codings_next.setIcon(qta.icon('mdi6.arrow-right'))
        self.ui.pushButton_show_codings_next.pressed.connect(self.show_selected_code_in_text_next)
        self.ui.pushButton_show_codings_prev.setIcon(qta.icon('mdi6.arrow-left'))
        self.ui.pushButton_show_codings_prev.pressed.connect(self.show_selected_code_in_text_previous)
        self.ui.pushButton_show_all_codings.setIcon(qta.icon('mdi6.text-search'))
        self.ui.pushButton_show_all_codings.pressed.connect(self.show_all_codes_in_text)
        self.ui.pushButton_show_all_codings.setIcon(qta.icon('mdi6.text-search', options=[{'scale-factor': 1.2}]))
        self.ui.pushButton_show_all_codings.pressed.connect(self.show_all_codes_in_text)
        self.ui.pushButton_important.setIcon(qta.icon('mdi6.star-outline', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_important.pressed.connect(self.show_important_coded)
        # Right hand side splitter buttons
        self.ui.pushButton_code_rule.setIcon(qta.icon('mdi6.text-shadow'))
        self.ui.pushButton_code_rule.pressed.connect(self.show_code_rule)
        self.ui.pushButton_journal.hide()
        self.ui.pushButton_project_memo.setIcon(qta.icon('mdi6.file-document-outline'))
        self.ui.pushButton_project_memo.pressed.connect(self.show_project_memo)
        self.ui.textEdit_info.tabChangesFocus()
        # Header buttons
        self.ui.pushButton_annotate.setIcon(qta.icon('mdi6.text-box-edit-outline', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_annotate.pressed.connect(self.annotate)
        self.ui.pushButton_show_annotations.setIcon(
            qta.icon('mdi6.text-search-variant', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_show_annotations.pressed.connect(self.show_annotations)
        self.ui.pushButton_coding_memo.setIcon(qta.icon('mdi6.text-box-edit', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_coding_memo.pressed.connect(self.coded_text_memo)
        self.ui.pushButton_show_memos.setIcon(qta.icon('mdi6.text-search', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_show_memos.pressed.connect(self.show_memos)
        self.ui.pushButton_auto_code.setIcon(qta.icon('mdi6.mace'))
        self.ui.pushButton_auto_code.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.pushButton_auto_code.customContextMenuRequested.connect(self.button_auto_code_menu)
        self.ui.pushButton_auto_code.clicked.connect(self.auto_code)
        self.ui.pushButton_auto_code_frag_this_file.setIcon(qta.icon('mdi6.magic-staff'))
        self.ui.pushButton_auto_code_frag_this_file.pressed.connect(self.auto_code_sentences)
        self.ui.pushButton_auto_code_surround.setIcon(qta.icon('mdi6.spear'))
        self.ui.pushButton_auto_code_surround.pressed.connect(self.button_autocode_surround)
        self.ui.pushButton_auto_code_undo.setIcon(qta.icon('mdi6.undo'))
        self.ui.pushButton_auto_code_undo.pressed.connect(self.undo_autocoding)
        self.ui.pushButton_default_new_code_color.setIcon(qta.icon('mdi6.palette', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_default_new_code_color.pressed.connect(self.set_default_new_code_color)
        self.ui.label_exports.setPixmap(qta.icon('mdi6.export').pixmap(22, 22))

        self.ui.lineEdit_search.textEdited.connect(self.search_for_text)
        self.ui.lineEdit_search.setEnabled(False)
        self.ui.checkBox_search_all_files.stateChanged.connect(self.search_for_text)
        self.ui.checkBox_search_all_files.setEnabled(False)
        self.ui.checkBox_search_case.stateChanged.connect(self.search_for_text)
        self.ui.checkBox_search_case.setEnabled(False)
        self.ui.label_search_regex.setPixmap(qta.icon('mdi6.help').pixmap(22, 22))
        self.ui.label_search_case_sensitive.setPixmap(qta.icon('mdi6.format-letter-case').pixmap(22, 22))
        self.ui.label_search_all_files.setPixmap(qta.icon('mdi6.text-box-multiple-outline').pixmap(22, 22))
        self.ui.label_font_size.setPixmap(qta.icon('mdi6.format-size').pixmap(22, 22))
        self.ui.pushButton_previous.setIcon(qta.icon('mdi6.arrow-left', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_previous.setEnabled(False)
        self.ui.pushButton_next.setIcon(qta.icon('mdi6.arrow-right', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_next.setEnabled(False)
        self.ui.pushButton_next.pressed.connect(self.move_to_next_search_text)
        self.ui.pushButton_previous.pressed.connect(self.move_to_previous_search_text)
        self.ui.pushButton_help.setIcon(qta.icon('mdi6.help'))
        self.ui.pushButton_help.pressed.connect(self.help)
        self.ui.pushButton_right_side_pane.setIcon(qta.icon('mdi6.arrow-expand-left', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_right_side_pane.pressed.connect(self.show_right_side_pane)
        self.ui.pushButton_delete_all_codes.setIcon(qta.icon('mdi6.delete-outline', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_delete_all_codes.pressed.connect(self.delete_all_codes_from_file)
        self.ui.pushButton_edit.setIcon(qta.icon('mdi6.text-box-edit-outline', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_edit.pressed.connect(self.edit_mode_toggle)
        self.ui.pushButton_exit_edit.setIcon(qta.icon('mdi6.text-box-check-outline', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_exit_edit.pressed.connect(self.edit_mode_toggle)
        self.ui.pushButton_undo_edit.setIcon(qta.icon('mdi6.undo', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_undo_edit.pressed.connect(self.undo_edited_text)
        self.ui.label_codes_count.setEnabled(False)
        self.ui.treeWidget.setDragEnabled(True)
        self.ui.treeWidget.setAcceptDrops(True)
        self.ui.treeWidget.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.ui.treeWidget.viewport().installEventFilter(self)
        self.ui.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.treeWidget.customContextMenuRequested.connect(self.tree_menu)
        #self.ui.treeWidget.itemSelectionChanged.connect(self.fill_code_label_with_selected_code)
        self.ui.treeWidget.itemPressed.connect(self.fill_code_label_with_selected_code)
        self.ui.comboBox_export.currentIndexChanged.connect(self.export_option_selected)
        index = self.ui.comboBox_fontsize.findText(str(self.app.settings['docfontsize']),
                                                   QtCore.Qt.MatchFlag.MatchFixedString)
        if index == -1:
            index = 0
        self.ui.comboBox_fontsize.setCurrentIndex(index)
        self.ui.comboBox_fontsize.currentTextChanged.connect(self.change_text_font_size)
        self.ui.splitter.setSizes([150, 400])
        try:
            s0 = int(self.app.settings['dialogcodetext_splitter0'])
            s1 = int(self.app.settings['dialogcodetext_splitter1'])
            if s0 > 5 and s1 > 5:
                self.ui.splitter.setSizes([s0, s1])
            v0 = int(self.app.settings['dialogcodetext_splitter_v0'])
            v1 = int(self.app.settings['dialogcodetext_splitter_v1'])
            if v0 > 5 and v1 > 5:
                # 30s are for the groupboxes containing buttons
                self.ui.leftsplitter.setSizes([v0, v1, 30])
        except KeyError:
            pass
        self.ui.splitter.splitterMoved.connect(self.update_sizes)
        self.ui.leftsplitter.splitterMoved.connect(self.update_sizes)

        # Add paragraph numbers widget
        self.number_bar = NumberBar(self.ui.textEdit)
        layout = QtWidgets.QVBoxLayout(self.ui.lineNumbers)
        layout.setContentsMargins(0, 0, 0, 0)  # Remove margins if needed
        layout.addWidget(self.number_bar)
        self.ui.lineNumbers.setLayout(layout)
        self.fill_tree()
        # These signals after the tree is filled the first time
        self.ui.treeWidget.itemCollapsed.connect(self.get_collapsed)
        self.ui.treeWidget.itemExpanded.connect(self.get_collapsed)

        # AI search
        self.ui.pushButton_ai_search.pressed.connect(self.ai_search_clicked)
        self.ui.listWidget_ai.selectionModel().selectionChanged.connect(self.ai_search_selection_changed)
        self.ai_search_listview_action_label = None
        self.ui.listWidget_ai.clicked.connect(self.ai_search_list_clicked)
        self.ui.ai_progressBar.setVisible(False)
        self.ui.ai_progressBar.setStyleSheet(f"""
            QProgressBar::chunk {{
                background-color: {self.app.highlight_color()};
            }}
        """)
        self.ai_search_spinner_sequence = ['', '.', '..', '...']
        self.ai_search_spinner_index = 0
        self.ai_search_spinner_timer = QtCore.QTimer(self)
        self.ai_search_spinner_timer.timeout.connect(self.ai_search_update_spinner)
        self.ai_search_prompt = None
        self.ai_search_ai_model = None
        self.ai_search_found = False
        self.ai_include_coded_segments = None
        self.ai_search_analysis_counter = 0

    @staticmethod
    def help():
        """ Open help for transcribe section in browser. """

        url = "https://github.com/ccbogel/QualCoder/wiki/4.1.-Coding-Text"
        webbrowser.open(url)

    def show_right_side_pane(self):
        """ Button press to show hidden right side pane. """

        self.ui.splitter.setSizes([150, 300, 100])

    def set_default_new_code_color(self):
        """ New code colours are usually generated randomly.
         This overrides the random approach, by setting a colout. """

        tmp_code = {'name': 'new', 'color': None}
        ui = DialogColorSelect(self.app, tmp_code)
        ok = ui.exec()
        if not ok:
            return
        color = ui.get_color()
        if color is not None:
            self.ui.pushButton_default_new_code_color.setStyleSheet(f'background-color: {color}')
        self.default_new_code_color = color

    def change_text_font_size(self):
        """ Combobox font size changed, range: 8 - 22 points. """

        font = f'font: {self.ui.comboBox_fontsize.currentText()}pt "{self.app.settings["font"]}";'
        self.ui.textEdit.setStyleSheet(font)

    def find_code_in_tree(self):
        """ Find a code by name in the codes tree and select it.
        """

        dialog = QtWidgets.QInputDialog(None)
        dialog.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog.setWindowTitle(_("Search for code"))
        dialog.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        msg = _("Find and select first code that matches text.") + "\n"
        msg += _("Enter text to match all or partial code:")
        dialog.setLabelText(msg)
        dialog.resize(200, 20)
        ok = dialog.exec()
        if not ok:
            return
        search_text = dialog.textValue()

        # Remove selections and search for matching item text
        self.ui.treeWidget.setCurrentItem(None)
        self.ui.treeWidget.clearSelection()
        item = None
        iterator = QtWidgets.QTreeWidgetItemIterator(self.ui.treeWidget)
        while iterator.value():
            item = iterator.value()
            if "cid" in item.text(1):
                cid = int(item.text(1)[4:])
                code_ = next((code_ for code_ in self.codes if code_['cid'] == cid), None)
                if search_text in code_['name']:
                    self.ui.treeWidget.setCurrentItem(item)
                    break
            iterator += 1
        if item is None:
            Message(self.app, _("Match not found"), _("No code with matching text found.")).exec()
            return
        # Expand parents
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def get_recent_codes(self):
        """ Get recently used codes. Must have loaded all codes first.
        recent codes are stored as space delimited text in project table.
        Add code id to recent codes list, if code is present. """

        self.recent_codes = []
        cur = self.app.conn.cursor()
        cur.execute("select recently_used_codes from project")
        res = cur.fetchone()
        if not res:
            return
        if res[0] == "" or res[0] is None:
            return
        recent_codes_text = res[0].split()
        for code_id in recent_codes_text:
            try:
                cid = int(code_id)
                for code_ in self.codes:
                    if cid == code_['cid']:
                        self.recent_codes.append(code_)
            except ValueError:
                pass

    def get_collapsed(self, item):
        """ On category collapse or expansion signal, find the collapsed parent category items.
        This will fill the self.app.collapsed_categories and is the expanded/collapsed tree is then replicated across
        other areas of the app. """

        #print(item.text(0), item.text(1), "Expanded:", item.isExpanded())
        if item.text(1)[:3] == "cid":
            return
        if not item.isExpanded() and item.text(1) not in self.app.collapsed_categories:
            self.app.collapsed_categories.append(item.text(1))
        if item.isExpanded() and item.text(1) in self.app.collapsed_categories:
            self.app.collapsed_categories.remove(item.text(1))

    def get_files(self, ids=None):
        """ Get files with additional details and fill list widget.
         Called by: init, get_files_from_attributes, show_files_like
         Args:
            ids : list, fill with ids to limit file selection.
         """

        if ids is None:
            ids = []
        self.ui.listWidget.clear()
        self.filenames = self.app.get_text_filenames(ids)
        # Fill additional details about each file in the memo
        cur = self.app.conn.cursor()
        sql = "select length(fulltext), fulltext from source where id=?"
        sql_codings = "select count(cid) from code_text where fid=? and owner=?"
        for file_ in self.filenames:
            cur.execute(sql, [file_['id'], ])
            res = cur.fetchone()
            if res is None:  # Safety catch
                res = [0, ""]
            tt = _("Characters: ") + str(res[0])
            file_['characters'] = res[0]
            file_['start'] = 0
            file_['end'] = res[0]
            file_['fulltext'] = res[1]
            cur.execute(sql_codings, [file_['id'], self.app.settings['codername']])
            res = cur.fetchone()
            tt += f'\n{_("Codings:")} {res[0]}'
            tt += f"\n{_('From:')} {file_['start']} - {file_['end']}"
            item = QtWidgets.QListWidgetItem(file_['name'])
            if file_['memo'] != "":
                tt += f"\nMemo: {file_['memo']}"
            item.setToolTip(tt)
            self.ui.listWidget.addItem(item)
        self.file_ = None
        self.code_text = []  # Must be before clearing textEdit, as next calls cursorChanged
        self.ui.textEdit.setText("")

    def update_file_tooltip(self):
        """ Create tooltip for file containing characters, codings and from: to: if partially loaded.
        Called by get_files, updates to add, remove codings, text edits.
        Requires self.file_ """

        if self.file_ is None:
            return
        cur = self.app.conn.cursor()
        sql = "select length(fulltext), fulltext from source where id=?"
        cur.execute(sql, [self.file_['id'], ])
        res = cur.fetchone()
        if res is None:  # Safety catch
            res = [0, ""]
        tt = _("Characters: ") + str(res[0])
        file_size = {'characters': res[0], 'start': 0, 'end': res[0], 'fulltext': res[1]}
        sql_codings = "select count(cid) from code_text where fid=? and owner=?"
        cur.execute(sql_codings, [self.file_['id'], self.app.settings['codername']])
        res = cur.fetchone()
        tt += f"\n{_('Codings:')} {res[0]}"
        tt += f"\n{_('From:')} {file_size['start']} - {file_size['end']}"
        if self.file_['memo'] != "":
            tt += f"\nMemo: {self.file_['memo']}"
        # Find item to update tooltip
        items = self.ui.listWidget.findItems(self.file_['name'], Qt.MatchFlag.MatchExactly)
        if len(items) == 0:
            return
        items[0].setToolTip(tt)

    def get_files_from_attributes(self):
        """ Select files based on attribute selections.
        Attribute results are a dictionary of:
        first item is a Boolean AND or OR list item
        Followed by each attribute list item
        """

        # Clear ui
        self.ui.pushButton_file_attributes.setToolTip(_("Attributes"))
        ui = DialogSelectAttributeParameters(self.app)
        ui.fill_parameters(self.attributes)
        temp_attributes = deepcopy(self.attributes)
        self.attributes = []
        ok = ui.exec()
        if not ok:
            self.attributes = temp_attributes
            self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable', options=[{'scale_factor': 1.3}]))
            self.ui.pushButton_file_attributes.setToolTip(_("Attributes"))
            if self.attributes:
                self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable-box', options=[{'scale_factor': 1.3}]))
            return
        self.attributes = ui.parameters
        if len(self.attributes) == 1:  # Boolean parameter, no attributes
            self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable', options=[{'scale_factor': 1.3}]))
            self.ui.pushButton_file_attributes.setToolTip(_("Attributes"))
            self.get_files()
            return
        if not ui.result_file_ids:
            Message(self.app, _("Nothing found") + " " * 20, _("No matching files found")).exec()
            self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable', options=[{'scale_factor': 1.3}]))
            self.ui.pushButton_file_attributes.setToolTip(_("Attributes"))
            return
        self.ui.pushButton_file_attributes.setIcon(qta.icon('mdi6.variable-box', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_file_attributes.setToolTip(ui.tooltip_msg)
        self.get_files(ui.result_file_ids)

    def update_sizes(self):
        """ Called by changed splitter size """

        sizes = self.ui.splitter.sizes()
        self.app.settings['dialogcodetext_splitter0'] = sizes[0]
        self.app.settings['dialogcodetext_splitter1'] = sizes[1]
        v_sizes = self.ui.leftsplitter.sizes()
        self.app.settings['dialogcodetext_splitter_v0'] = v_sizes[0]
        self.app.settings['dialogcodetext_splitter_v1'] = v_sizes[1]

    def fill_code_label_with_selected_code(self):
        """ Fill code label with currently selected item's code name and colour.
         Also, if text is highlighted, assign the text to this code.

         Called by: treewidgetitem_clicked """

        current = self.ui.treeWidget.currentItem()
        if current is None:
            return
        # Extra to fill right-hand side splitter details
        self.show_code_rule()
        if current.text(1)[0:3] == 'cat':
            self.ui.label_code.hide()
            self.ui.label_code.setToolTip("")
            return
        self.ui.label_code.show()
        # Set background colour of label to code color, and store current code for underlining
        for c in self.codes:
            if int(current.text(1)[4:]) == c['cid']:
                fg_color = TextColor(c['color']).recommendation
                style = f"QLabel {{background-color:{c['color']}; color: {fg_color};}}"
                self.ui.label_code.setStyleSheet(style)
                self.ui.label_code.setAutoFillBackground(True)
                tt = f"{c['name']}\n"
                if c['memo'] != "":
                    tt += _("Memo: ") + c['memo']
                self.ui.label_code.setToolTip(tt)
                break
        selected_text = self.ui.textEdit.textCursor().selectedText()
        if len(selected_text) > 0:
            self.mark()
        # When a code is selected undo the show selected code features
        self.highlight()
        # Reload button icons as they disappear on Windows
        self.ui.pushButton_show_codings_prev.setIcon(qta.icon('mdi6.arrow-left', options=[{'scale_factor': 1.3}]))
        self.ui.pushButton_show_codings_next.setIcon(qta.icon('mdi6.arrow-right', options=[{'scale_factor': 1.3}]))

    def fill_tree(self):
        """ Fill tree widget, top level items are main categories and unlinked codes.
        The Count column counts the number of times that code has been used by selected coder in selected file.
        Keep record of non-expanded items, then re-enact these items when treee fill is called again. """

        cats = deepcopy(self.categories)
        codes = deepcopy(self.codes)
        self.ui.treeWidget.clear()
        self.ui.treeWidget.setColumnCount(4)
        self.ui.treeWidget.setHeaderLabels([_("Name"), _("Id"), _("Memo"), _("Count")])
        self.ui.treeWidget.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.ui.treeWidget.header().resizeSection(0, 400)
        if not self.app.settings['showids']:
            self.ui.treeWidget.setColumnHidden(1, True)
        else:
            self.ui.treeWidget.setColumnHidden(1, False)
        self.ui.treeWidget.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.ui.treeWidget.header().setStretchLastSection(False)
        # Add top level categories
        remove_list = []
        for c in cats:
            if c['supercatid'] is None:
                memo = ""
                if c['memo'] != "":
                    memo = _("Memo")
                top_item = QtWidgets.QTreeWidgetItem([c['name'], 'catid:' + str(c['catid']), memo])
                top_item.setToolTip(2, c['memo'])
                top_item.setToolTip(0, '')
                if len(c['name']) > 52:
                    top_item.setText(0, f"{c['name'][:25]}..{c['name'][-25:]}")
                    top_item.setToolTip(0, c['name'])
                self.ui.treeWidget.addTopLevelItem(top_item)
                if f"catid:{c['catid']}" in self.app.collapsed_categories:
                    top_item.setExpanded(False)
                else:
                    top_item.setExpanded(True)
                remove_list.append(c)
        for item in remove_list:
            cats.remove(item)
        ''' Add child categories. look at each unmatched category, iterate through tree
         to add as child, then remove matched categories from the list '''
        count = 0
        while len(cats) > 0 and count < 10000:
            remove_list = []
            for c in cats:
                it = QtWidgets.QTreeWidgetItemIterator(self.ui.treeWidget)
                item = it.value()
                count2 = 0
                while item and count2 < 10000:  # while there is an item in the list
                    if item.text(1) == f"catid:{c['supercatid']}":
                        memo = ""
                        if c['memo'] != "":
                            memo = _("Memo")
                        child = QtWidgets.QTreeWidgetItem([c['name'], f"catid:{c['catid']}", memo])
                        child.setToolTip(2, c['memo'])
                        child.setToolTip(0, '')
                        if len(c['name']) > 52:
                            child.setText(0, f"{c['name'][:25]}..{c['name'][-25:]}")
                            child.setToolTip(0, c['name'])
                        item.addChild(child)
                        if f"catid:{c['catid']}" in self.app.collapsed_categories:
                            child.setExpanded(False)
                        else:
                            child.setExpanded(True)
                        remove_list.append(c)
                    it += 1
                    item = it.value()
                    count2 += 1
            for item in remove_list:
                cats.remove(item)
            count += 1
        # Add unlinked codes as top level items
        remove_items = []
        for c in codes:
            if c['catid'] is None:
                memo = ""
                if c['memo'] != "":
                    memo = _("Memo")
                top_item = QtWidgets.QTreeWidgetItem([c['name'], f"cid:{c['cid']}", memo])
                top_item.setToolTip(2, c['memo'])
                top_item.setToolTip(0, c['name'])
                if len(c['name']) > 52:
                    top_item.setText(0, f"{c['name'][:25]}..{c['name'][-25:]}")
                    top_item.setToolTip(0, c['name'])
                top_item.setBackground(0, QBrush(QColor(c['color']), Qt.BrushStyle.SolidPattern))
                color = TextColor(c['color']).recommendation
                top_item.setForeground(0, QBrush(QColor(color)))
                top_item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable |
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
                self.ui.treeWidget.addTopLevelItem(top_item)
                remove_items.append(c)
        for item in remove_items:
            codes.remove(item)
        # Add codes as children
        for c in codes:
            it = QtWidgets.QTreeWidgetItemIterator(self.ui.treeWidget)
            item = it.value()
            count = 0
            while item and count < 10000:
                if item.text(1) == f"catid:{c['catid']}":
                    memo = ""
                    if c['memo'] != "":
                        memo = _("Memo")
                    child = QtWidgets.QTreeWidgetItem([c['name'], f"cid:{c['cid']}", memo])
                    child.setToolTip(2, c['memo'])
                    child.setToolTip(0, c['name'])
                    if len(c['name']) > 52:
                        child.setText(0, f"{c['name'][:25]}..{c['name'][-25:]}")
                        child.setToolTip(0, c['name'])
                    child.setBackground(0, QBrush(QColor(c['color']), Qt.BrushStyle.SolidPattern))
                    color = TextColor(c['color']).recommendation
                    child.setForeground(0, QBrush(QColor(color)))
                    child.setFlags(
                        Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable |
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled)
                    item.addChild(child)
                    c['catid'] = -1  # Make unmatchable
                it += 1
                item = it.value()
                count += 1
        # self.ui.treeWidget.expandAll()
        if self.tree_sort_option == "all asc":
            self.ui.treeWidget.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)
        if self.tree_sort_option == "all desc":
            self.ui.treeWidget.sortByColumn(0, QtCore.Qt.SortOrder.DescendingOrder)
        self.fill_code_counts_in_tree()

    def fill_code_counts_in_tree(self):
        """ Count instances of each code for current coder and in the selected file.
        If the tab 'AI assisted coding' is active, the codings will be counted
        across all files, not only the currently selected one, because the AI assisted 
        coding is not working on a per-file basis.    
        Called by: fill_tree, tab_changed
        """

        ai_mode = self.ui.tabWidget.currentIndex() == 1
        cur = self.app.conn.cursor()
        if ai_mode:
            sql = "select count(cid) from code_text where cid=? and owner=?"
        else:  # documents
            sql = "select count(cid) from code_text where cid=? and fid=? and owner=?"
        it = QtWidgets.QTreeWidgetItemIterator(self.ui.treeWidget)
        item = it.value()
        count = 0
        while item and count < 10000:
            if item.text(1)[0:4] == "cid:":
                cid = str(item.text(1)[4:])
                try:
                    if ai_mode:
                        cur.execute(sql, [cid, self.app.settings['codername']])
                        result = cur.fetchone()
                    else:  # document mode
                        if self.file_ is None:
                            result = [0]  # will delete the count because no file is selected
                        else:
                            cur.execute(sql, [cid, self.file_['id'], self.app.settings['codername']])
                            result = cur.fetchone()
                    if result[0] > 0:
                        item.setText(3, str(result[0]))
                        item.setToolTip(3, self.app.settings['codername'])
                    else:
                        item.setText(3, "")
                except Exception as e:
                    msg = f"Fill code counts error\n{e}\n{sql}\ncid: {cid}\n"
                    if not ai_mode:
                        msg += f"self.file_['id']: {self.file_['id']}\n"
                    else:
                        msg += '(AI assisted coding)\n'
                    logger.debug(msg)
                    item.setText(3, "")
            it += 1
            item = it.value()
            count += 1

    def get_codes_and_categories(self):
        """ Called from init, delete category/code.
        Also called on other coding dialogs in the dialog_list. """

        self.codes, self.categories = self.app.get_codes_categories()

    # Right Hand Side splitter details for code rule, project memo
    def show_code_rule(self):
        """ Show text in right-hand side splitter pane. """

        self.ui.textEdit_info.setPlainText("")
        selected = self.ui.treeWidget.currentItem()
        if selected is None:
            return
        self.project_memo = False
        self.code_rule = True
        self.ui.label_info.setText(selected.text(0))
        txt = ""
        if selected.text(1)[0:3] == 'cat':
            for c in self.categories:
                if c['catid'] == int(selected.text(1)[6:]):
                    txt += c['memo']
                    break
        else:  # Code is selected
            for c in self.codes:
                if c['cid'] == int(selected.text(1)[4:]):
                    txt += c['memo']
                    break
            self.ui.textEdit_info.show()
            # Get coded examples
            txt += "\n\n" + _("Examples:") + "\n"
            cur = self.app.conn.cursor()
            cur.execute("select seltext from code_text where length(seltext) > 0 and cid=? order by random() limit 3",
                        [int(selected.text(1)[4:])])
            res = cur.fetchall()
            for i, r in enumerate(res):
                txt += f"{i + 1}: {r[0]}\n"
        self.ui.textEdit_info.setReadOnly(True)
        self.ui.textEdit_info.blockSignals(True)
        self.ui.textEdit_info.setText(txt)

    def show_project_memo(self):
        """ Show project memo in right-hand side splitter pane """

        cur = self.app.conn.cursor()
        cur.execute("select memo from project")
        res = cur.fetchone()
        self.project_memo = True
        self.code_rule = False
        self.ui.label_info.setText(_("Project memo"))
        self.ui.textEdit_info.setText(res[0])

    # Header section widgets
    def delete_all_codes_from_file(self):
        """ Delete all codes from this file by this coder. """

        if self.file_ is None:
            return
        msg = _("Delete all codings in this file made by ") + self.app.settings['codername']
        ui = DialogConfirmDelete(self.app, msg)
        ok = ui.exec()
        if not ok:
            return
        cur = self.app.conn.cursor()
        sql = "delete from code_text where fid=? and owner=?"
        cur.execute(sql, (self.file_['id'], self.app.settings['codername']))
        self.app.conn.commit()
        self.get_coded_text_update_eventfilter_tooltips()
        self.app.delete_backup = False
        msg = _("All codes by ") + self.app.settings['codername'] + _(" deleted from ") + self.file_['name']
        self.parent_textEdit.append(msg)

    # Search for text methods
    def search_for_text(self):
        """ Find indices of matching text.
        Resets current search_index.
        If all files is checked then searches for all matching text across all text files
        and displays the file text and current position to user.
        If case-sensitive is checked then text searched is matched for case sensitivity.
        search_type start search options 3,or 5 chars.
        Enter pressed is also a search option.
        """

        if self.file_ is None:
            return
        if not self.search_indices:
            self.ui.pushButton_next.setEnabled(False)
            self.ui.pushButton_previous.setEnabled(False)
        self.search_indices = []
        self.search_index = -1
        self.search_term = self.ui.lineEdit_search.text()
        if self.search_type == 3 and len(self.search_term) < 3:
            self.ui.label_search_totals.setText("")
            return
        if self.search_type == 5 and len(self.search_term) < 5:
            self.ui.label_search_totals.setText("")
            return
        self.ui.label_search_totals.setText("0 / 0")
        pattern = None
        flags = 0
        if not self.ui.checkBox_search_case.isChecked():
            flags |= re.IGNORECASE
        try:
            pattern = re.compile(self.search_term, flags)
        except re.error as e_:
            logger.warning('re error Bad escape ' + str(e_))
        if pattern is None:
            return
        self.search_indices = []
        if self.ui.checkBox_search_all_files.isChecked():
            """ Search for this text across all files. """
            for filedata in self.app.get_file_texts():
                try:
                    text_ = filedata['fulltext']
                    for match in pattern.finditer(text_):
                        self.search_indices.append((filedata, match.start(), len(match.group(0))))
                except re.error:
                    logger.exception('Failed searching text %s for %s', filedata['name'], self.search_term)
        else:
            try:
                if self.text:
                    for match in pattern.finditer(self.text):
                        # Get result as first dictionary item
                        source_name = self.app.get_file_texts([self.file_['id'], ])[0]
                        self.search_indices.append((source_name, match.start(), len(match.group(0))))
            except re.error:
                logger.exception('Failed searching current file for %s', self.search_term)
        if len(self.search_indices) > 0:
            self.ui.pushButton_next.setEnabled(True)
            self.ui.pushButton_previous.setEnabled(True)
        self.ui.label_search_totals.setText(f"0 / {len(self.search_indices)}")

    def move_to_next_search_text(self):
        """ Push button pressed to move to next search text position. """

        if self.file_ is None or self.search_indices == []:
            return
        self.search_index += 1
        if self.search_index >= len(self.search_indices):
            self.search_index = 0
        cursor = self.ui.textEdit.textCursor()
        next_result = self.search_indices[self.search_index]
        # next_result is a tuple containing a dictionary of
        # ({name, id, fullltext, memo, owner, date}, char position, search string length)
        if self.file_ is None or self.file_['id'] != next_result[0]['id']:
            for x in range(self.ui.listWidget.count()):
                if self.ui.listWidget.item(x).text() == next_result[0]['name']:
                    self.ui.listWidget.blockSignals(True)
                    self.ui.listWidget.setCurrentRow(x)
                    self.ui.listWidget.blockSignals(False)
            self.load_file(next_result[0])
            self.ui.lineEdit_search.setText(self.search_term)
        cursor.setPosition(cursor.position() + next_result[2])
        self.ui.textEdit.setTextCursor(cursor)

        # Highlight selected text
        cursor.setPosition(next_result[1])
        cursor.setPosition(cursor.position() + next_result[2], QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.ui.textEdit.setTextCursor(cursor)
        self.ui.label_search_totals.setText(f"{self.search_index + 1} / {len(self.search_indices)}")
        self.scroll_text_into_view()

    def move_to_previous_search_text(self):
        """ Push button pressed to move to previous search text position. """

        if self.file_ is None or self.search_indices == []:
            return
        self.search_index -= 1
        if self.search_index < 0:
            self.search_index = len(self.search_indices) - 1
        cursor = self.ui.textEdit.textCursor()
        prev_result = self.search_indices[self.search_index]
        # prev_result is a tuple containing a dictionary of
        # (name, id, fullltext, memo, owner, date) and char position and search string length
        if self.file_ is None or self.file_['id'] != prev_result[0]['id']:
            self.load_file(prev_result[0])
            self.ui.lineEdit_search.setText(self.search_term)
        cursor.setPosition(prev_result[1])
        cursor.setPosition(cursor.position() + prev_result[2], QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.ui.textEdit.setTextCursor(cursor)
        self.ui.label_search_totals.setText(f"{self.search_index + 1} / {len(self.search_indices)}")
        self.scroll_text_into_view()

    def lineedit_search_menu(self, position):
        """ Option to change from automatic search on 3 characters or 5 character to search.
         Enter is alway a search option. """

        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        action_char3 = QtGui.QAction(_("Automatic search 3 or more characters"))
        action_char5 = QtGui.QAction(_("Automatic search 5 or more characters"))
        if self.search_type != 3:
            menu.addAction(action_char3)
        if self.search_type != 5:
            menu.addAction(action_char5)
        action = menu.exec(self.ui.lineEdit_search.mapToGlobal(position))
        if action is None:
            return
        if action == action_char3:
            self.search_type = 3
            self.ui.lineEdit_search.textEdited.connect(self.search_for_text)
            # self.ui.lineEdit_search.returnPressed.disconnect(self.search_for_text)
            return
        if action == action_char5:
            self.search_type = 5
            self.ui.lineEdit_search.textEdited.connect(self.search_for_text)
            # self.ui.lineEdit_search.returnPressed.disconnect(self.search_for_text)
            return

    def button_auto_code_menu(self, position):
        """ Options to auto-code all instances, first instance or last instance in a file. """

        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        msg = ""
        if self.all_first_last == "all":
            msg = " *"
        else:
            msg = ""
        action_all = QtGui.QAction(_("all matches in file") + msg)
        if self.all_first_last == "first":
            msg = " *"
        else:
            msg = ""
        action_first = QtGui.QAction(_("first match in file") + msg)
        if self.all_first_last == "last":
            msg = " *"
        else:
            msg = ""
        action_last = QtGui.QAction(_("last match in file") + msg)
        menu.addAction(action_all)
        menu.addAction(action_first)
        menu.addAction(action_last)
        action = menu.exec(self.ui.pushButton_auto_code.mapToGlobal(position))
        if action is None:
            return
        if action == action_all:
            self.all_first_last = "all"
        if action == action_first:
            self.all_first_last = "first"
        if action == action_last:
            self.all_first_last = "last"

    def text_edit_recent_codes_menu(self, position):
        """ Alternative context menu.
        Shows a list of recent codes to select from.
        Called by R key press in the text edit pane, only if there is some selected text. """

        if self.ui.textEdit.toPlainText() == "":
            return
        selected_text = self.ui.textEdit.textCursor().selectedText()
        if selected_text == "":
            return
        if len(self.recent_codes) == 0:
            return
        menu = QtWidgets.QMenu()
        for item in self.recent_codes:
            menu.addAction(item['name'])
        action = menu.exec(self.ui.textEdit.mapToGlobal(position))
        if action is None:
            return
        # Remaining actions will be the submenu codes
        self.recursive_set_current_item(self.ui.treeWidget.invisibleRootItem(), action.text())
        self.mark()

    def text_edit_menu(self, position):
        """ Context menu for textEdit.
        Mark, unmark, annotate, copy, memo coded, coded importance. """

        if self.ui.textEdit.toPlainText() == "" or self.edit_mode:
            return
        cursor = self.ui.textEdit.cursorForPosition(position)
        selected_text = self.ui.textEdit.textCursor().selectedText()
        menu = QtWidgets.QMenu()
        menu.setStyleSheet('font-size:' + str(self.app.settings['fontsize']) + 'pt')
        menu.setToolTipsVisible(True)
        action_annotate = None
        action_copy = None
        action_code_memo = None
        action_edit_annotate = None
        action_important = None
        action_mark = None
        action_not_important = None
        action_change_code = None
        action_start_pos = None
        action_end_pos = None
        action_change_pos = None
        action_unmark = None
        action_new_code = None
        action_new_invivo_code = None
        submenu_ai_text_analysis = None

        # Can have multiple coded text at this position
        for item in self.code_text:
            if cursor.position() + self.file_['start'] >= item['pos0'] and cursor.position() <= item['pos1']:
                action_unmark = QtGui.QAction(_("Unmark (U)"))
                action_code_memo = QtGui.QAction(_("Memo coded text (M)"))
                action_start_pos = QtGui.QAction(_("Change start position (SHIFT LEFT/ALT RIGHT)"))
                action_end_pos = QtGui.QAction(_("Change end position (SHIFT RIGHT/ALT LEFT)"))
                # action_change_pos = QtGui.QAction(_("Change code position key presses"))
                if item['important'] is None or item['important'] > 1:
                    action_important = QtGui.QAction(_("Add important mark (I)"))
                if item['important'] == 1:
                    action_not_important = QtGui.QAction(_("Remove important mark"))
                action_change_code = QtGui.QAction(_("Change code"))
        if selected_text != "":
            if self.ui.treeWidget.currentItem() is not None:
                action_mark = menu.addAction(_("Mark (Q)"))
            # Use up to 10 recent codes
            if len(self.recent_codes) > 0:
                submenu = menu.addMenu(_("Mark with recent code (R)"))
                for item in self.recent_codes:
                    submenu.addAction(item['name'])
            action_new_code = menu.addAction(_("Mark with new code (N)"))
            action_new_invivo_code = menu.addAction(_("in vivo code (V)"))

        if action_unmark:
            menu.addAction(action_unmark)
        if action_code_memo:
            menu.addAction(action_code_memo)
        if action_important:
            menu.addAction(action_important)
        if action_not_important:
            menu.addAction(action_not_important)
        if action_change_pos:
            menu.addAction(action_change_pos)
        if action_start_pos:
            menu.addAction(action_start_pos)
        if action_end_pos:
            menu.addAction(action_end_pos)
        if action_change_code:
            menu.addAction(action_change_code)
        action_annotate = menu.addAction(_("Annotate (A)"))
        action_copy = menu.addAction(_("Copy to clipboard"))
        if selected_text == "" and self.is_annotated(cursor.position()):
            action_edit_annotate = menu.addAction(_("Edit annotation"))
        action_set_bookmark = menu.addAction(_("Set bookmark (B)"))
        if selected_text != "":
            submenu_ai_text_analysis = menu.addMenu(_("AI Text Analysis"))
            submenu_ai_text_analysis.setToolTipsVisible(True)
            if self.app.ai is not None and self.app.ai.is_ready():
                submenu_ai_text_analysis.setEnabled(True)
                prompts_list = PromptsList(self.app, 'text_analysis')
                for prompt in prompts_list.prompts:
                    ac = submenu_ai_text_analysis.addAction(prompt.name_and_scope())
                    ac.setToolTip(prompt.description)
                    ac.setProperty('submenu', 'ai_text_analysis')
                    ac.setData(prompt)
                submenu_ai_text_analysis.addSeparator()
                ac = submenu_ai_text_analysis.addAction(_('Edit text analysis prompts'))
                ac.setProperty('submenu', 'ai_text_analysis_prompts')
            else:
                submenu_ai_text_analysis.setEnabled(False)
        action_hide_top_groupbox = None
        action_show_top_groupbox = None
        if self.ui.groupBox.isHidden():
            action_show_top_groupbox = menu.addAction(_("Show control panel (H)"))
        if not self.ui.groupBox.isHidden():
            action_hide_top_groupbox = menu.addAction(_("Hide control panel (H)"))

        action = menu.exec(self.ui.textEdit.mapToGlobal(position))
        if action is None:
            return
        if action == action_important:
            self.set_important(cursor.position())
            return
        if action == action_not_important:
            self.set_important(cursor.position(), False)
            return
        if selected_text != "" and action == action_copy:
            self.copy_selected_text_to_clipboard()
            return
        if selected_text != "" and self.ui.treeWidget.currentItem() is not None and action == action_mark:
            self.mark()
            return
        if action == action_annotate:
            self.annotate()
            return
        if action == action_edit_annotate:
            # Used fora point text press rather than a selected text
            self.annotate(cursor.position())
            return
        if action == action_unmark:
            self.unmark(cursor.position())
            return
        if action == action_code_memo:
            self.coded_text_memo(cursor.position())
            return
        if action == action_start_pos:
            self.change_code_start_or_end_position(cursor.position(), "start")
            return
        if action == action_end_pos:
            self.change_code_start_or_end_position(cursor.position(), "end")
            return
        if action == action_set_bookmark:
            cur = self.app.conn.cursor()
            bookmark_pos = cursor.position() + self.file_['start']
            cur.execute("update project set bookmarkfile=?, bookmarkpos=?", [self.file_['id'], bookmark_pos])
            self.app.conn.commit()
            return
        if action == action_change_code:
            self.change_code_to_another_code(cursor.position())
            return
        if action == action_show_top_groupbox:
            self.ui.groupBox.setVisible(True)
            return
        if action == action_hide_top_groupbox:
            self.ui.groupBox.setVisible(False)
            return
        if action == action_new_code:
            self.mark_with_new_code()
            return
        if action == action_new_invivo_code:
            self.mark_with_new_code(in_vivo=True)
            return
        if action.property('submenu') == 'ai_text_analysis':
            if self.file_ is None:
                Message(self.app, _('Warning'), _("No file was selected"), "warning").exec()
                return
            selected_text = self.ui.textEdit.textCursor().selectedText()
            start_pos = self.ui.textEdit.textCursor().selectionStart() + self.file_['start']
            ai_chat_signal_emitter.newTextChatSignal.emit(int(self.file_['id']),
                                                          self.file_['name'],
                                                          selected_text,
                                                          start_pos,
                                                          action.data())
            return
        if action.property('submenu') == 'ai_text_analysis_prompts':
            ui = DialogAiEditPrompts(self.app, 'text_analysis')
            ui.exec()
            return
        # Remaining actions will be the submenu codes
        self.recursive_set_current_item(self.ui.treeWidget.invisibleRootItem(), action.text())
        self.mark()

    def change_code_start_or_end_position(self, position, start_or_end):
        """ Change start or end pos of code.
        Args:
            position: Integer - text cursor position
            start_or_end: String - 'start' or 'end'
        """

        if self.file_ is None:
            return
        coded_list = []
        for item in self.code_text:
            if item['pos0'] <= position + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername']:
                coded_list.append(item)
        if not coded_list:
            return
        code_ = []
        if len(coded_list) == 1:
            code_ = coded_list[0]
        # Multiple codes at this position to select from
        if len(coded_list) > 1:
            ui = DialogSelectItems(self.app, coded_list, _("Select codes"), "single")
            ok = ui.exec()
            if not ok:
                return
            code_ = ui.get_selected()
        if not code_:
            return

        cur = self.app.conn.cursor()
        length_sql = "select length(fulltext) from source where id=?"
        cur.execute(length_sql, [self.file_['id']])
        fulltext_length = cur.fetchone()[0]
        title = f"Adjust code {start_or_end}"
        adjustment, ok = QtWidgets.QInputDialog.getInt(self, title, code_['name'])
        if not ok:
            return
        if start_or_end == "start":
            code_['pos0'] += adjustment
            if code_['pos0'] < 0:
                code_['pos0'] = 0
            if code_['pos0'] >= code_['pos1']:
                code_['pos0'] = code_['pos1'] - 1
        if start_or_end == "end":
            code_['pos1'] += adjustment
            if code_['pos1'] <= code_['pos0']:
                code_['pos1'] = code_['pos0'] + 1
            if code_['pos1'] > fulltext_length:
                code_['pos1'] = fulltext_length - 1
        text_sql = "select substr(fulltext,?,?), length(fulltext) from source where id=?"
        cur.execute(text_sql, [code_['pos0'], code_['pos1'], self.file_['id']])
        seltext = cur.fetchone()[0]
        sql = "update code_text set pos0=?, pos1=?, seltext=? where ctid=?"
        cur.execute(sql, [code_['pos0'], code_['pos1'], seltext, code_['ctid']])
        self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def mark_with_new_code(self, in_vivo=False):
        """ Create new code and mark selected text.
        Called through text_edit_menu or N key press - with selected text.
        Args:
            in_vivo : Boolean if True use in vivo text selection as code name """

        # Get selected category, if any
        tree_item = self.ui.treeWidget.currentItem()
        catid = None
        if tree_item is not None and tree_item.text(1)[0:3] == 'cat':
            catid = int(tree_item.text(1)[6:])
        codes_copy = deepcopy(self.codes)
        if not in_vivo:
            self.add_code(catid)
        else:
            self.add_code(catid, code_name=self.ui.textEdit.textCursor().selectedText())
        new_code = None
        for c in self.codes:
            if c not in codes_copy:
                new_code = c
        if new_code is None and not in_vivo:
            # Not a new code and not an in vivo coding
            return
        if new_code is None and in_vivo:
            # Find existing code name that matches in vivo selection
            new_code = None
            for c in self.codes:
                if c['name'] == self.ui.textEdit.textCursor().selectedText():
                    new_code = c
        self.recursive_set_current_item(self.ui.treeWidget.invisibleRootItem(), new_code['name'])
        self.mark()

    def change_code_to_another_code(self, position):
        """ Change code to another code.
        Args:
            position: Integer - text cursor position
        """

        # Get coded segments at this position
        if self.file_ is None:
            return
        coded_text_list = []
        for item in self.code_text:
            if item['pos0'] <= position + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername']:
                coded_text_list.append(item)
        if not coded_text_list:
            return
        text_item = []
        if len(coded_text_list) == 1:
            text_item = coded_text_list[0]
        # Multiple codes at this position to select from
        if len(coded_text_list) > 1:
            ui = DialogSelectItems(self.app, coded_text_list, _("Select codes"), "single")
            ok = ui.exec()
            if not ok:
                return
            text_item = ui.get_selected()
        if not text_item:
            return
        # Get replacement code
        codes_list = deepcopy(self.codes)
        to_remove = None
        for code_ in codes_list:
            if code_['cid'] == text_item['cid']:
                to_remove = code_
        if to_remove:
            codes_list.remove(to_remove)
        ui = DialogSelectItems(self.app, codes_list, _("Select replacement code"), "single")
        ok = ui.exec()
        if not ok:
            return
        replacement_code = ui.get_selected()
        if not replacement_code:
            return
        cur = self.app.conn.cursor()
        sql = "update code_text set cid=? where ctid=?"
        try:
            cur.execute(sql, [replacement_code['cid'], text_item['ctid']])
            self.app.conn.commit()
        except sqlite3.IntegrityError:
            pass
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def recursive_set_current_item(self, item, text_):
        """ Set matching item to be the current selected item.
        Recurse through any child categories.
        Tried to use QTreeWidget.finditems - but this did not find matching item text
        Called by: textEdit recent codes menu option
        Required for: mark()
        Args:
            item : QTreeWidgetItem - usually root
            text_ : String
        """

        child_count = item.childCount()
        for i in range(child_count):
            if item.child(i).text(1)[0:3] == "cid" and (item.child(i).text(0) == text_
                                                        or item.child(i).toolTip(0) == text_):
                self.ui.treeWidget.setCurrentItem(item.child(i))
            self.recursive_set_current_item(item.child(i), text_)

    def is_annotated(self, position):
        """ Check if position is annotated to provide annotation menu option.
        Args:
            position: Integer - location in text document
        Returns:
            True or False
        """

        for note in self.annotations:
            if (note['pos0'] <= position + self.file_['start'] <= note['pos1']) \
                    and note['fid'] == self.file_['id']:
                return True
        return False

    def set_important(self, position, important=True):
        """ Set or unset importance to coded text.
        Importance is denoted using '1'
        Args:
            position: textEdit character cursor position
            important: boolean, default True """

        # Need to get coded segments at this position
        if position is None:
            # Called via button
            position = self.ui.textEdit.textCursor().position()
        if self.file_ is None:
            return
        coded_text_list = []
        for item in self.code_text:
            if item['pos0'] <= position + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername'] and \
                    ((not important and item['important'] == 1) or (important and item['important'] != 1)):
                coded_text_list.append(item)
        if not coded_text_list:
            return
        text_items = []
        if len(coded_text_list) == 1:
            text_items = [coded_text_list[0]]
        # Multiple codes at this position to select from
        if len(coded_text_list) > 1:
            ui = DialogSelectItems(self.app, coded_text_list, _("Select codes"), "multi")
            ok = ui.exec()
            if not ok:
                return
            text_items = ui.get_selected()
        if not text_items:
            return
        importance = None
        if important:
            importance = 1
        cur = self.app.conn.cursor()
        sql = "update code_text set important=? where ctid=?"
        for item in text_items:
            cur.execute(sql, [importance, item['ctid']])
            self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def active_file_memo(self):
        """ Send active file to file_memo method.
        Called by pushButton_document_memo for loaded text.
        """

        self.file_memo(self.file_)

    def file_memo(self, file_):
        """ Open file memo to view or edit.
        Called by pushButton_document_memo for loaded text, via active_file_memo
        and through file_menu for any file.
        Args:
            file_ : Dictionary of file values
        """

        if file_ is None:
            return
        ui = DialogMemo(self.app, _("Memo for file: ") + file_['name'], file_['memo'])
        ui.exec()
        memo = ui.memo
        if memo == file_['memo']:
            return
        file_['memo'] = memo
        cur = self.app.conn.cursor()
        cur.execute("update source set memo=? where id=?", (memo, file_['id']))
        self.app.conn.commit()
        items = self.ui.listWidget.findItems(file_['name'], Qt.MatchFlag.MatchExactly)
        if len(items) == 1:
            tt = items[0].toolTip()
            memo_pos = (tt.find(_("Memo:")))
            new_tt = f"{tt[:memo_pos]} {_('Memo:')} {file_['memo']}"
            items[0].setToolTip(new_tt)
        self.app.delete_backup = False

    def coded_text_memo(self, position=None):
        """ Add or edit a memo for this coded text.
        Args:
            position : Current text cursor position
        """

        if position is None:
            # Called via button
            position = self.ui.textEdit.textCursor().position()
        if self.file_ is None:
            return
        coded_text_list = []
        for item in self.code_text:
            if item['pos0'] <= position + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername']:
                coded_text_list.append(item)
        if not coded_text_list:
            return
        text_item = None
        if len(coded_text_list) == 1:
            text_item = coded_text_list[0]
        # Multiple codes at this position to select from
        if len(coded_text_list) > 1:
            ui = DialogSelectItems(self.app, coded_text_list, _("Select code to memo"), "single")
            ok = ui.exec()
            if not ok:
                return
            text_item = ui.get_selected()
        if text_item is None:
            return
        # Dictionary with cid fid seltext owner date name color memo
        msg = f"{text_item['name']} [{text_item['pos0']} - {text_item['pos1']}]"
        ui = DialogMemo(self.app, _("Memo for Coded text: ") + msg, text_item['memo'], "show", text_item['seltext'])
        ui.exec()
        memo = ui.memo
        if memo == text_item['memo']:
            return
        cur = self.app.conn.cursor()
        cur.execute("update code_text set memo=? where cid=? and fid=? and seltext=? and pos0=? and pos1=? and owner=?",
                    (memo, text_item['cid'], text_item['fid'], text_item['seltext'], text_item['pos0'],
                     text_item['pos1'],
                     text_item['owner']))
        self.app.conn.commit()
        for i in self.code_text:
            if text_item['cid'] == i['cid'] and text_item['seltext'] == i['seltext'] \
                    and text_item['pos0'] == i['pos0'] and text_item['pos1'] == i['pos1'] \
                    and text_item['owner'] == self.app.settings['codername']:
                i['memo'] = memo
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def shift_code_positions(self, position):
        """ After a text file is edited - text added or deleted, code positions may be inaccurate.
         enter a positive or negative integer to shift code positions for all codes after a click position in the
         document.
         Activated by ^ At key press
         Args:
            position : Integer - location  in text, characters
         """

        if self.file_ is None:
            return
        code_list = []
        for item in self.code_text:
            if item['pos0'] > position + self.file_['start']:
                code_list.append(item)
        if not code_list:
            return
        int_dialog = QtWidgets.QInputDialog()
        int_dialog.setMinimumSize(60, 150)
        # Remove context flag does not work here
        int_dialog.setWindowFlags(int_dialog.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        msg = _("Shift codings after clicked position")
        int_dialog.setWhatsThis(msg)
        int_dialog.setToolTip(msg)
        msg2 = _("Shift code positions for all codes after you have clicked on a position in the text.\n"
                 "Back up the project before running this action.\n"
                 "This function will help if you have edited the coded text and the codes are out of position.\n"
                 "Positive numbers (moves right) or negative numbers (moves left) (-500 to 500)\n"
                 "Clicked character position: ") + str(position)
        delta_shift, ok = int_dialog.getInt(self, msg, msg2, 0, -500, 500, 1)
        if not ok:
            return
        if delta_shift == 0:
            return
        int_dialog.done(1)  # Need this, as reactivated when called again with same int value.
        cur = self.app.conn.cursor()
        length_sql = "select length(fulltext) from source where id=?"
        cur.execute(length_sql, [self.file_['id']])
        fulltext_length = cur.fetchone()[0]
        text_sql = "select substr(fulltext,?,?), length(fulltext) from source where id=?"
        # Update code_text rows in database
        for coded in code_list:
            # print(coded['seltext'], coded['pos0'], coded['pos1'])
            new_pos0 = coded['pos0'] + delta_shift
            new_pos1 = coded['pos1'] + delta_shift
            # Get seltext and update if coded pos0 and pos1 are within bounds
            if new_pos0 > -1 and new_pos1 < fulltext_length:
                cur.execute(text_sql, [new_pos0, new_pos1 - new_pos0, self.file_['id']])
                seltext = cur.fetchone()[0]
                # print("len", fulltext_length, seltext)
                sql = "update code_text set pos0=?, pos1=?, seltext=? where ctid=?"
                cur.execute(sql, [new_pos0, new_pos1, seltext, coded['ctid']])
                self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def copy_selected_text_to_clipboard(self):
        """ Copy text to clipboard for external use.
        For example adding text to another document. """

        selected_text = self.ui.textEdit.textCursor().selectedText()
        cb = QtWidgets.QApplication.clipboard()
        cb.setText(selected_text)

    def tree_menu(self, position):
        """ Context menu for treewidget code/category items.
        Add, rename, memo, move or delete code or category. Change code color.
        Assign selected text to current hovered code. """

        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        selected = self.ui.treeWidget.currentItem()
        action_add_code_to_category = None
        action_add_category_to_category = None
        action_merge_category = None
        if selected is not None and selected.text(1)[0:3] == 'cat':
            action_add_code_to_category = menu.addAction(_("Add new code to category"))
            action_add_category_to_category = menu.addAction(_("Add a new category to category"))
            action_merge_category = menu.addAction(_("Merge category into category"))
        action_add_code = menu.addAction(_("Add a new code"))
        action_add_category = menu.addAction(_("Add a new category"))
        action_rename = menu.addAction(_("Rename"))
        action_edit_memo = menu.addAction(_("View or edit memo"))
        action_delete = menu.addAction(_("Delete"))
        action_color = None
        action_show_coded_media = None
        action_move_code = None
        if selected is not None and selected.text(1)[0:3] == 'cid':
            action_color = menu.addAction(_("Change code color"))
            action_show_coded_media = menu.addAction(_("Show coded files"))
            action_move_code = menu.addAction(_("Move code to"))
        action_show_codes_like = menu.addAction(_("Show codes like"))
        action_show_codes_of_colour = menu.addAction(_("Show codes of colour"))
        action_all_asc = menu.addAction(_("Sort ascending"))
        action_all_desc = menu.addAction(_("Sort descending"))
        action_cat_then_code_asc = menu.addAction(_("Sort category then code ascending"))

        action = menu.exec(self.ui.treeWidget.mapToGlobal(position))
        if action is not None:
            if action == action_all_asc:
                self.tree_sort_option = "all asc"
                self.fill_tree()
            if action == action_all_desc:
                self.tree_sort_option = "all desc"
                self.fill_tree()
            if action == action_cat_then_code_asc:
                self.tree_sort_option = "cat and code asc"
                self.fill_tree()
            if action == action_show_codes_like:
                self.show_codes_like()
                return
            if action == action_show_codes_of_colour:
                self.show_codes_of_color()
                return
            if selected is not None and action == action_color:
                self.change_code_color(selected)
            if action == action_add_category:
                self.add_category()
            if action == action_add_code:
                self.add_code()
            if action == action_merge_category:
                catid = int(selected.text(1).split(":")[1])
                self.merge_category(catid)
            if action == action_add_code_to_category:
                catid = int(selected.text(1).split(":")[1])
                self.add_code(catid)
            if action == action_add_category_to_category:
                catid = int(selected.text(1).split(":")[1])
                self.add_category(catid)
            if selected is not None and action == action_move_code:
                self.move_code(selected)
            if selected is not None and action == action_rename:
                self.rename_category_or_code(selected)
            if selected is not None and action == action_edit_memo:
                self.add_edit_cat_or_code_memo(selected)
            if selected is not None and action == action_delete:
                self.delete_category_or_code(selected)
            if selected is not None and action == action_show_coded_media:
                found_code = None
                tofind = int(selected.text(1)[4:])
                for code in self.codes:
                    if code['cid'] == tofind:
                        found_code = code
                        break
                if found_code:
                    self.coded_media_dialog(found_code)

    def recursive_non_merge_item(self, item, no_merge_list):
        """ Find matching item to be the current selected item.
        Recurse through any child categories.
        Tried to use QTreeWidget.finditems - but this did not find matching item text
        Called by: textEdit recent codes menu option
        Required for: merge_category()
        Args:
            item : QTreeWidgetItem
            no_merge_list : List of child Category ids (as Strings)
        """

        child_count = item.childCount()
        for i in range(child_count):
            if item.child(i).text(1)[0:3] == "cat":
                no_merge_list.append(item.child(i).text(1)[6:])
            self.recursive_non_merge_item(item.child(i), no_merge_list)
        return no_merge_list

    def merge_category(self, catid):
        """ Select another category to merge this category into.
        Args:
            catid : Integer category identifier
        """

        do_not_merge_list = []
        do_not_merge_list = self.recursive_non_merge_item(self.ui.treeWidget.currentItem(), do_not_merge_list)
        do_not_merge_list.append(str(catid))
        do_not_merge_ids_string = "(" + ",".join(do_not_merge_list) + ")"
        sql = "select name, catid, supercatid from code_cat where catid not in "
        sql += do_not_merge_ids_string + " order by name"
        cur = self.app.conn.cursor()
        cur.execute(sql)
        res = cur.fetchall()
        category_list = [{'name': "", 'catid': None, 'supercatid': None}]
        for r in res:
            category_list.append({'name': r[0], 'catid': r[1], "supercatid": r[2]})
        ui = DialogSelectItems(self.app, category_list, _("Select blank or category"), "single")
        ok = ui.exec()
        if not ok:
            return
        category = ui.get_selected()
        try:
            for code in self.codes:
                if code['catid'] == catid:
                    cur.execute("update code_name set catid=? where catid=?", [category['catid'], catid])
            cur.execute("delete from code_cat where catid=?", [catid])
            self.update_dialog_codes_and_categories()
            for cat in self.categories:
                if cat['supercatid'] == catid:
                    cur.execute("update code_cat set supercatid=? where supercatid=?", [category['catid'], catid])
            # Clear any orphan supercatids
            sql = "select supercatid from code_cat where supercatid not in (select catid from code_cat)"
            cur.execute(sql)
            orphans = cur.fetchall()
            sql = "update code_cat set supercatid=Null where supercatid=?"
            for orphan in orphans:
                cur.execute(sql, [orphan[0]])
            self.app.conn.commit()
        except Exception as e_:
            print(e_)
            self.app.conn.rollback()  # Revert all changes
            self.update_dialog_codes_and_categories()
            raise
        self.update_dialog_codes_and_categories()

    def move_code(self, selected):
        """ Move code to another category or to no category.
        Uses a list selection.
        Args:
            selected : QTreeWidgetItem
         """

        cid = int(selected.text(1)[4:])
        cur = self.app.conn.cursor()
        cur.execute("select name, catid from code_cat order by name")
        res = cur.fetchall()
        category_list = [{'name': "", 'catid': None}]
        for r in res:
            category_list.append({'name': r[0], 'catid': r[1]})
        ui = DialogSelectItems(self.app, category_list, _("Select blank or category"), "single")
        ok = ui.exec()
        if not ok:
            return
        category = ui.get_selected()
        cur.execute("update code_name set catid=? where cid=?", [category['catid'], cid])
        self.app.conn.commit()
        self.update_dialog_codes_and_categories()

    def show_memos(self):
        """ Show all memos for coded text in dialog. """

        if self.file_ is None:
            return
        text_ = ""
        cur = self.app.conn.cursor()
        sql = "select code_name.name, pos0,pos1, seltext, code_text.memo "
        sql += "from code_text join code_name on code_text.cid = code_name.cid "
        sql += "where length(code_text.memo)>0 and fid=? and code_text.owner=? order by pos0"
        cur.execute(sql, [self.file_['id'], self.app.settings['codername']])
        res = cur.fetchall()
        if not res:
            return
        for r in res:
            text_ += f'[{r[1]}-{r[2]}] ' + _("Code: ") + f'{r[0]}\n'
            text_ += _("Text: ") + f"{r[3]}\n"
            text_ += _("Memo: ") + f"{r[4]}\n\n"
        ui = DialogMemo(self.app, _("Memos for file: ") + self.file_['name'], text_)
        ui.ui.pushButton_clear.hide()
        ui.ui.textEdit.setReadOnly(True)
        ui.exec()

    def show_annotations(self):
        """ Show all annotations for text in dialog. """

        if self.file_ is None:
            return
        text_ = ""
        cur = self.app.conn.cursor()
        sql = "select substr(source.fulltext,pos0+1 ,pos1-pos0), pos0, pos1, annotation.memo "
        sql += "from annotation join source on annotation.fid = source.id "
        sql += "where fid=? and annotation.owner=? order by pos0"
        cur.execute(sql, [self.file_['id'], self.app.settings['codername']])
        res = cur.fetchall()
        if not res:
            return
        for r in res:
            text_ += f"[{r[1]}-{r[2]}] \n"
            text_ += _("Text: ") + f"{r[0]}\n"
            text_ += _("Annotation: ") + r[3] + "\n\n"
        ui = DialogMemo(self.app, _("Annotations for file: ") + self.file_['name'], text_)
        ui.ui.pushButton_clear.hide()
        ui.ui.textEdit.setReadOnly(True)
        ui.exec()

    def show_important_coded(self):
        """ Show codes flagged as important. """

        self.important = not self.important
        if self.important:
            self.ui.pushButton_important.setToolTip(_("Showing important codings"))
            self.ui.pushButton_important.setIcon(qta.icon('mdi6.star'))
        else:
            self.ui.pushButton_important.setToolTip(_("Show codings flagged important"))
            self.ui.pushButton_important.setIcon(qta.icon('mdi6.star-outline'))
        self.get_coded_text_update_eventfilter_tooltips()

    def show_codes_like(self):
        """ Show all codes if text is empty.
         Show selected codes that contain entered text.
         The input dialog is too narrow, so it is re-created. """

        dialog = QtWidgets.QInputDialog(None)
        dialog.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog.setWindowTitle(_("Show some codes"))
        dialog.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog.setLabelText(_("Show codes containing the text. (Blank for all)"))
        dialog.resize(200, 20)
        ok = dialog.exec()
        if not ok:
            return
        text_ = str(dialog.textValue())
        root = self.ui.treeWidget.invisibleRootItem()
        self.recursive_traverse(root, text_)

    def show_codes_of_color(self):
        """ Show all codes in colour range in code tree., ir all codes if no selection.
        Show selected codes that are of a selected colour.
        """

        ui = DialogSelectItems(self.app, colour_ranges, _("Select code colors"), "single")
        ok = ui.exec()
        if not ok:
            return
        selected_color = ui.get_selected()
        show_codes_of_colour_range(self.app, self.ui.treeWidget, self.codes, selected_color)

    def recursive_traverse(self, item, text_):
        """ Find all children codes of this item that match or not and hide or unhide based on 'text'.
        Looks at tooltip also because the code text may be shortened to 50 characters for display, and the tooltip
        is not shortened.
        Recurse through all child categories.
        Called by: show_codes_like
        Args:
            item: a QTreeWidgetItem
            text_:  Text string for matching with code names
        """

        child_count = item.childCount()
        for i in range(child_count):
            if "cid:" in item.child(i).text(1) and len(text_) > 0 and text_ not in item.child(i).text(0) and \
                    text_ not in item.child(i).toolTip(0):
                item.child(i).setHidden(True)
            if "cid:" in item.child(i).text(1) and text_ == "":
                item.child(i).setHidden(False)
            self.recursive_traverse(item.child(i), text_)

    def keyPressEvent(self, event):
        """
        Ctrl Z Undo last unmarking
        Ctrl F jump to search box
        A annotate - for current selection
        Q Quick Mark with code - for current selection
        B Create bookmark - at clicked position
        H Hide / Unhide top groupbox
        I Tag important
        L Show codes like
        M memo code - at clicked position
        N new code
        O Shortcut to cycle through overlapping codes - at clicked position
        S search text - may include current selection
        R opens a context menu for recently used codes for marking text
        U Unmark at selected location
        V assign 'in vivo' code to selected text
        Ctrl 0 to Ctrl 9 - button presses
        ! Display Clicked character position
        ^ Alt key. Shift code positions. May be needed after the text is edited
            (added or deleted) to shift subsequent codings.
        """

        key = event.key()
        mods = event.modifiers()

        # Ctrl + F jump to search box
        if key == QtCore.Qt.Key.Key_F and mods == QtCore.Qt.KeyboardModifier.ControlModifier:
            self.ui.lineEdit_search.setFocus()
            return
        # Ctrl Z undo last unmarked coding
        if key == QtCore.Qt.Key.Key_Z and mods == QtCore.Qt.KeyboardModifier.ControlModifier:
            self.undo_last_unmarked_code()
            return
        # Ctrl 0 to 9
        if mods & QtCore.Qt.KeyboardModifier.ControlModifier:
            if key == QtCore.Qt.Key.Key_1:
                self.go_to_next_file()
                return
            if key == QtCore.Qt.Key.Key_2:
                self.go_to_latest_coded_file()
                return
            if key == QtCore.Qt.Key.Key_3:
                self.go_to_bookmark()
                return
            if key == QtCore.Qt.Key.Key_4:
                self.file_memo(self.file_)
                return
            if key == QtCore.Qt.Key.Key_5:
                self.get_files_from_attributes()
                return
            if key == QtCore.Qt.Key.Key_6:
                self.show_selected_code_in_text_previous()
                return
            if key == QtCore.Qt.Key.Key_7:
                self.show_selected_code_in_text_next()
                return
            if key == QtCore.Qt.Key.Key_8:
                self.show_all_codes_in_text()
                return
            if key == QtCore.Qt.Key.Key_9:
                self.show_important_coded()
                return
            if key == QtCore.Qt.Key.Key_0:
                self.help()
                return

        if not self.ui.textEdit.hasFocus():
            return
        # Ignore all other key events if edit mode is active
        if self.edit_mode:
            return
        key = event.key()
        # mod = QtGui.QGuiApplication.keyboardModifiers()
        cursor_pos = self.ui.textEdit.textCursor().position()
        selected_text = self.ui.textEdit.textCursor().selectedText()
        codes_here = []
        for item in self.code_text:
            if item['pos0'] <= cursor_pos + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername']:
                codes_here.append(item)
        # Hash display character position
        if key == QtCore.Qt.Key.Key_Exclam:
            Message(self.app, _("Text position") + " " * 20, _("Character position: ") + str(cursor_pos)).exec()
            return
        if key == QtCore.Qt.Key.Key_Dollar:
            self.shift_code_positions(self.ui.textEdit.textCursor().position() + self.file_['start'])
            return
        # Annotate selected
        if key == QtCore.Qt.Key.Key_A and selected_text != "":
            self.annotate()
            return
        # Bookmark
        if key == QtCore.Qt.Key.Key_B and self.file_ is not None:
            text_pos = self.ui.textEdit.textCursor().position() + self.file_['start']
            cur = self.app.conn.cursor()
            cur.execute("update project set bookmarkfile=?, bookmarkpos=?", [self.file_['id'], text_pos])
            self.app.conn.commit()
            return
        # Hide unHide top groupbox
        if key == QtCore.Qt.Key.Key_H:
            self.ui.groupBox.setHidden(not (self.ui.groupBox.isHidden()))
            return
        # Important  for coded text
        if key == QtCore.Qt.Key.Key_I:
            self.set_important(cursor_pos)
            return
        # Show codes like
        if key == QtCore.Qt.Key.Key_L:
            self.show_codes_like()
        # Memo for current code
        if key == QtCore.Qt.Key.Key_M:
            self.coded_text_memo(cursor_pos)
            return
        if key == QtCore.Qt.Key.Key_N and self.ui.textEdit.textCursor().selectedText() != '':
            self.mark_with_new_code(False)
            return
        # Overlapping codes cycle
        now = datetime.datetime.now()
        overlap_diff = now - self.overlap_timer
        if key == QtCore.Qt.Key.Key_O and len(self.overlaps_at_pos) > 0 and overlap_diff.microseconds > 150000:
            self.overlap_timer = datetime.datetime.now()
            self.highlight_selected_overlap()
            return
        # Quick mark selected
        if key == QtCore.Qt.Key.Key_Q and selected_text != "":
            self.mark()
            return
        # Unmark at text position
        if key == QtCore.Qt.Key.Key_U:
            self.unmark(cursor_pos)
            return
        # Create or assign in vivo code to selected text
        if key == QtCore.Qt.Key.Key_V and selected_text != "":
            self.mark_with_new_code(in_vivo=True)
            return
        # Recent codes context menu
        if key == QtCore.Qt.Key.Key_R and self.file_ is not None and self.ui.textEdit.textCursor().selectedText() != "":
            self.text_edit_recent_codes_menu(self.ui.textEdit.cursorRect().topLeft())
            return
        # Search, with or without selected
        if key == QtCore.Qt.Key.Key_S and self.file_ is not None:
            if selected_text == "":
                self.ui.lineEdit_search.setFocus()

    def highlight_selected_overlap(self):
        """ Highlight the current overlapping text code, by placing formatting on top. """

        self.overlaps_at_pos_idx += 1
        if self.overlaps_at_pos_idx >= len(self.overlaps_at_pos):
            self.overlaps_at_pos_idx = 0
        item = self.overlaps_at_pos[self.overlaps_at_pos_idx]
        # Remove formatting
        cursor = self.ui.textEdit.textCursor()
        cursor.setPosition(int(item['pos0'] - self.file_['start']), QtGui.QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(int(item['pos1'] - self.file_['start']), QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(QtGui.QTextCharFormat())
        # Reapply formatting
        color = ""
        for code in self.codes:
            if code['cid'] == item['cid']:
                color = code['color']
        fmt = QtGui.QTextCharFormat()
        brush = QBrush(QColor(color))
        fmt.setBackground(brush)
        if item['important']:
            fmt.setFontWeight(QtGui.QFont.Weight.Bold)
        fmt.setForeground(QBrush(QColor(TextColor(color).recommendation)))
        cursor.setCharFormat(fmt)
        self.apply_underline_to_overlaps()

    def overlapping_codes_in_text(self):
        """ When coded text is clicked on.
        Only enabled if two or more codes are here.
        Adjust for when portion of full text file loaded.
        Called by: textEdit cursor position changed. """

        self.overlaps_at_pos = []
        self.overlaps_at_pos_idx = 0
        pos = self.ui.textEdit.textCursor().position()
        for item in self.code_text:
            if item['pos0'] <= pos + self.file_['start'] <= item['pos1']:
                # logger.debug("Code name for selected pos0:" + str(item['pos0'])+" pos1:"+str(item['pos1'])
                self.overlaps_at_pos.append(item)
        if len(self.overlaps_at_pos) < 2:
            self.overlaps_at_pos = []
            self.overlaps_at_pos_idx = 0

    def export_option_selected(self):
        """ ComboBox export option selected. """

        export_option = self.ui.comboBox_export.currentText()
        if export_option == "":
            return
        if export_option == "html":
            self.export_html_file()
        if export_option == "odt":
            self.export_odt_file()
        if export_option == "txt":
            self.export_tagged_text()
        self.ui.comboBox_export.setCurrentIndex(0)

    def export_odt_file(self):
        """ Export text to open document format with .odt ending.
        QTextWriter supports plaintext, ODF and HTML.
        Cannot export tooltips.
        Called by export_option_selected
        """

        if len(self.ui.textEdit.document().toPlainText()) == 0:
            return
        filename = f"{self.file_['name']}.odt"
        exp_dir = ExportDirectoryPathDialog(self.app, filename)
        filepath = exp_dir.filepath
        if filepath is None:
            return
        tw = QtGui.QTextDocumentWriter()
        tw.setFileName(filepath)
        tw.setFormat(b'ODF')  # byte array needed for Windows 10
        tw.write(self.ui.textEdit.document())
        msg = _("Coded text file exported: ") + filepath
        self.parent_textEdit.append(msg)
        Message(self.app, _('Coded text file exported'), msg, "information").exec()

    def export_html_file(self):
        """ Export text to html file.
        Called by export_option_selected.
        """

        if len(self.ui.textEdit.document().toPlainText()) == 0:
            return
        plain_text = self.ui.textEdit.document().toPlainText()
        # Prepare code text with name and ordering
        code_text2 = deepcopy(self.code_text)
        code_ids_used = []
        for ct in code_text2:
            for c in self.codes:
                if ct['cid'] == c['cid']:
                    ct['codename'] = html.escape(c['name'])
                    code_ids_used.append(c['cid'])
                    break
        code_ids_used = list(set(code_ids_used))

        # Prepare html text
        html_text = '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">\
        <html><head><meta charset="utf-8" /></head>\
        <body style=" font-family:"Noto Sans"; font-size:10pt; font-weight:400; font-style:normal;">'
        for i, c in enumerate(plain_text):
            if c == "\n":
                c = "<br />\n"
            else:
                if c in entities.keys():
                    c = entities[c]  # html.escape(c)
            for ct in code_text2:
                if ct['pos0'] == i:
                    title = ct['codename']
                    if ct['important'] == 1:
                        title += "\nIMPORTANT"
                    if ct['memo'] is not None and ct['memo'] != "":
                        title += f"\nMEMO: {ct['memo']}"
                    html_text += f'<span title="{title}" style="color:#000000; background-color:{ct["color"]};">'
                if ct['pos1'] == i:
                    html_text += "</span>"
            html_text += c  # Some encoding issues, e.g. the Euro symbol
        # Add Codes list
        codes_directory = []
        for cd in self.codes:
            if cd['cid'] in code_ids_used:
                category = None
                for cat in self.categories:
                    if cd['catid'] == cat['catid']:
                        category = cat['name']
                codes_directory.append([cd['name'], cd['color'], cd['memo'], category])
        html_text += "<br /><br /><h2>Codes list</h2>\n"
        for cd in codes_directory:
            html_text += f'<p><span style="background-color:{cd[1]}">&nbsp;&nbsp;&nbsp;</span> &nbsp;<b>{cd[0]}</b>'
            if cd[3] is not None:
                html_text += f"&nbsp;CATEGORY: {cd[3]}"
            if cd[2] != "":
                html_text += f"&nbsp;&nbsp;CODE MEMO: {cd[2]}"
            html_text += '</p>'

        html_text += "\n</body></html>"
        html_filename = self.file_['name'] + ".html"
        exp_dir = ExportDirectoryPathDialog(self.app, html_filename)
        filepath = exp_dir.filepath
        if filepath is None:
            return
        with open(filepath, 'w') as html_file:
            html_file.write(html_text)
        msg = _("Coded text file exported to: ") + filepath
        self.parent_textEdit.append(msg)
        Message(self.app, _('Coded html file exported'), msg, "information").exec()

    def export_tagged_text(self):
        """ Export a text file with code tags.
         code tags are surrounded by double braces:
         {{codename{{some coded text}}codename}}. """

        if len(self.ui.textEdit.document().toPlainText()) == 0:
            return
        plain_text = self.ui.textEdit.document().toPlainText()
        # Prepare code text with name and ordering
        code_text2 = deepcopy(self.code_text)
        code_ids_used = []
        for ct in code_text2:
            for c in self.codes:
                if ct['cid'] == c['cid']:
                    ct['codename'] = c['name']
                    code_ids_used.append(c['cid'])
                    break
        code_ids_used = list(set(code_ids_used))

        # Prepare text
        tagged_text = ""
        for i, c in enumerate(plain_text):
            for ct in code_text2:
                if ct['pos0'] == i:
                    tagged_text += "{{" + ct['codename'] + "{{"
                if ct['pos1'] == i:
                    tagged_text += "}}" + ct['codename'] + "}}"
            tagged_text += c
        # Add Codes list
        codes_list = []
        for cd in self.codes:
            if cd['cid'] in code_ids_used:
                category = None
                for cat in self.categories:
                    if cd['catid'] == cat['catid']:
                        category = cat['name']
                codes_list.append([cd['name'], cd['memo'], category])
        tagged_text += "\n\n\nCODES LIST\n"
        for cd in codes_list:
            tagged_text += cd[0]
            if cd[2] is not None:
                tagged_text += f" -- CATEGORY: {cd[2]}"
            if cd[1] != "":
                tagged_text += f" -- CODE MEMO: {cd[1]}"
            tagged_text += '\n'

        filename = self.file_['name'] + "_tagged.txt"
        exp_dir = ExportDirectoryPathDialog(self.app, filename)
        filepath = exp_dir.filepath
        if filepath is None:
            return
        with open(filepath, 'w') as text_file:
            text_file.write(tagged_text)
        msg = _("Coded text file exported to: ") + filepath
        self.parent_textEdit.append(msg)
        Message(self.app, _('Coded text file exported'), msg, "information").exec()

    def eventFilter(self, object_, event):
        """ Using this event filter to identify treeWidgetItem drop events.
        http://doc.qt.io/qt-5/qevent.html#Type-enum
        QEvent::Drop 63 A drag and drop operation is completed (QDropEvent).
        https://stackoverflow.com/questions/28994494/why-does-qtreeview-not-fire-a-drop-or-move-event-during-drag-and-drop

        Also use it to detect key events in the textedit.
        These are used to extend or shrink a text coding.
        Only works if clicked on a code (text cursor is in the coded text).
        Shrink start and end code positions using alt arrow left and alt arrow right
        Extend start and end code positions using shift arrow left, shift arrow right
        Ctrl E Enter and exit Edit Mode
        """

        if object_ is self.ui.treeWidget.viewport():
            # If a show selected code was active, then clicking on a code in code tree, shows all codes and all tooltips
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                self.show_all_codes_in_text()
            if event.type() == QtCore.QEvent.Type.Drop:
                item = self.ui.treeWidget.currentItem()
                # event position is QPointF, itemAt requires toPoint
                parent = self.ui.treeWidget.itemAt(event.position().toPoint())
                self.item_moved_update_data(item, parent)
                return True
        # Change start and end code positions using alt arrow left and alt arrow right
        # and shift arrow left, shift arrow right

        if type(event) == QtGui.QKeyEvent and object_ is self.ui.textEdit:
            key = event.key()
            mod = event.modifiers()
            first_key_press = event.type() == QtCore.QEvent.Type.KeyPress and not event.isAutoRepeat()
            # using timer for a lot of things
            now = datetime.datetime.now()
            diff = now - self.code_resize_timer
            if diff.microseconds < 100000:
                if mod in (QtCore.Qt.KeyboardModifier.AltModifier, QtCore.Qt.KeyboardModifier.ShiftModifier) \
                      and key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
                    return True # consume rapid shift + left clicks, etc. without changing selection
                else:
                    return False
            # Ctrl + E Edit mode - must be detected here as Ctrl E is overridden in editable textEdit
            if key == QtCore.Qt.Key.Key_E and mod == QtCore.Qt.KeyboardModifier.ControlModifier and first_key_press:
                self.edit_mode_toggle()
                return True
            # Ignore all other key events if edit mode is active
            if self.edit_mode:
                return False
            cursor_pos = self.ui.textEdit.textCursor().position()
            codes_here = []
            for item in self.code_text:
                if item['pos0'] <= cursor_pos + self.file_['start'] <= item['pos1'] and \
                        item['owner'] == self.app.settings['codername']:
                    codes_here.append(item)
            code_ = None
            if len(codes_here) == 0:
                return False
            if len(codes_here) > 1 and mod in (
                    QtCore.Qt.KeyboardModifier.AltModifier, QtCore.Qt.KeyboardModifier.ShiftModifier) \
                    and key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
                ui = DialogSelectItems(self.app, codes_here, _("Select a code"), "single")
                ok = ui.exec()
                if not ok:
                    return
                code_ = ui.get_selected()
                if not code_:
                    return
            if len(codes_here) == 1:
                code_ = codes_here[0]
            # Key event can be too sensitive, adjusted  for 150 millisecond gap
            self.code_resize_timer = datetime.datetime.now()
            if key == QtCore.Qt.Key.Key_Left and mod == QtCore.Qt.KeyboardModifier.AltModifier:
                self.shrink_to_left(code_)
                return True
            if key == QtCore.Qt.Key.Key_Right and mod == QtCore.Qt.KeyboardModifier.AltModifier:
                self.shrink_to_right(code_)
                return True
            if key == QtCore.Qt.Key.Key_Left and mod == QtCore.Qt.KeyboardModifier.ShiftModifier:
                self.extend_left(code_)
                return True
            if key == QtCore.Qt.Key.Key_Right and mod == QtCore.Qt.KeyboardModifier.ShiftModifier:
                self.extend_right(code_)
                return True
        return False

    def extend_left(self, code_):
        """ Shift left arrow.
        Args:
            code_: code Dictionary
        """

        if not code_:
            return
        if code_['pos0'] < 1:
            return
        code_['pos0'] -= 1
        cur = self.app.conn.cursor()
        text_sql = "select substr(fulltext,?,?) from source where id=?"
        cur.execute(text_sql, [code_['pos0'] + 1, code_['pos1'] - code_['pos0'], code_['fid']])
        seltext = cur.fetchone()[0]
        sql = "update code_text set pos0=?, seltext=? where ctid=?"
        cur.execute(sql, (code_['pos0'], seltext, code_['ctid']))
        self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def extend_right(self, code_):
        """ Shift right arrow.
        Args:
            code_: code Dictionary
        """

        if not code_:
            return
        if code_['pos1'] + 1 >= len(self.ui.textEdit.toPlainText()):
            return
        code_['pos1'] += 1
        cur = self.app.conn.cursor()
        text_sql = "select substr(fulltext,?,?) from source where id=?"
        cur.execute(text_sql, [code_['pos0'] + 1, code_['pos1'] - code_['pos0'], code_['fid']])
        seltext = cur.fetchone()[0]
        sql = "update code_text set pos1=?, seltext=? where ctid=?"
        cur.execute(sql,
                    (code_['pos1'], seltext, code_['ctid']))
        self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def shrink_to_left(self, code_):
        """ Alt left arrow, shrinks code from the right end of the code.
        Args:
            code_: code Dictionary
        """

        if not code_:
            return
        if code_['pos1'] <= code_['pos0'] + 1:
            return
        code_['pos1'] -= 1
        cur = self.app.conn.cursor()
        text_sql = "select substr(fulltext,?,?) from source where id=?"
        cur.execute(text_sql, [code_['pos0'] + 1, code_['pos1'] - code_['pos0'], code_['fid']])
        seltext = cur.fetchone()[0]
        sql = "update code_text set pos1=?, seltext=? where ctid=?"
        cur.execute(sql, (code_['pos1'], seltext, code_['ctid']))
        self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def shrink_to_right(self, code_):
        """ Alt right arrow shrinks code from the left end of the code.
        Args:
            code_: code Dictionary
        """

        if not code_:
            return
        if code_['pos0'] >= code_['pos1'] - 1:
            return
        code_['pos0'] += 1
        cur = self.app.conn.cursor()
        text_sql = "select substr(fulltext,?,?) from source where id=?"
        cur.execute(text_sql, [code_['pos0'] + 1, code_['pos1'] - code_['pos0'], code_['fid']])
        seltext = cur.fetchone()[0]
        sql = "update code_text set pos0=?, seltext=? where ctid=?"
        cur.execute(sql, (code_['pos0'], seltext, code_['ctid']))
        self.app.conn.commit()
        self.app.delete_backup = False
        self.get_coded_text_update_eventfilter_tooltips()

    def show_selected_code_in_text_next(self):
        """ Highlight only the selected code in the text. Move to next instance in text
        from the current textEdit cursor position.
        Adjust for a portion of text loaded.
        Called by: pushButton_show_codings_next
        """

        if self.file_ is None:
            return
        item = self.ui.treeWidget.currentItem()
        if item is None or item.text(1)[0:3] == 'cat':
            return
        cid = int(item.text(1)[4:])
        # Index list has to be dynamic, as a new code_text item could be created before this method is called again
        # Develop indices and tooltip coded text list
        indexes = []
        tt_code_text = []
        for ct in self.code_text:
            if ct['cid'] == cid:
                indexes.append(ct)
                tt_code_text.append(ct)
        indexes = sorted(indexes, key=itemgetter('pos0'))
        cursor = self.ui.textEdit.textCursor()
        cur_pos = cursor.position()
        end_pos = 0
        found_larger = False
        msg = "/" + str(len(indexes))
        for i, index in enumerate(indexes):
            if index['pos0'] - self.file_['start'] > cur_pos:
                cur_pos = index['pos0'] - self.file_['start']
                end_pos = index['pos1'] - self.file_['start']
                found_larger = True
                msg = str(i + 1) + msg
                break
        if not found_larger and indexes == []:
            return
        # Loop around to the highest index
        if not found_larger and indexes != []:
            cur_pos = indexes[0]['pos0'] - self.file_['start']
            end_pos = indexes[0]['pos1'] - self.file_['start']
            msg = "1" + msg
        if not found_larger:
            cursor = self.ui.textEdit.textCursor()
            cursor.setPosition(0)
            self.ui.textEdit.setTextCursor(cursor)
        self.unlight()
        msg = " " + _("Code:") + " " + msg
        # Highlight the code in the text
        color = ""
        for c in self.codes:
            if c['cid'] == cid:
                color = c['color']
        cursor.setPosition(cur_pos)
        self.ui.textEdit.setTextCursor(cursor)
        cursor.setPosition(cur_pos, QtGui.QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(end_pos, QtGui.QTextCursor.MoveMode.KeepAnchor)
        brush = QBrush(QColor(color))
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(brush)
        foreground_color = TextColor(color).recommendation
        fmt.setForeground(QBrush(QColor(foreground_color)))
        cursor.mergeCharFormat(fmt)
        # Update tooltips to show only this code
        self.eventFilterTT.set_codes_and_annotations(self.app, tt_code_text, self.codes, self.annotations,
                                                     self.file_)
        # Need to reload icons as they disappear on Windows
        self.ui.pushButton_show_all_codings.setIcon(qta.icon('mdi6.grid'))
        self.ui.pushButton_show_codings_prev.setStyleSheet(f"background-color: {color}; color:{foreground_color}")
        self.ui.pushButton_show_codings_prev.setIcon(qta.icon('mdi6.arrow-left'))
        tt = _("Show previous coding of selected code") + msg
        self.ui.pushButton_show_codings_prev.setToolTip(tt)
        self.ui.pushButton_show_codings_next.setStyleSheet(f"background-color: {color}; color:{foreground_color}")
        tt = _("Show next coding of selected code") + msg
        self.ui.pushButton_show_codings_next.setToolTip(tt)
        self.ui.pushButton_show_codings_next.setIcon(qta.icon('mdi6.arrow-right'))

    def show_selected_code_in_text_previous(self):
        """ Highlight only the selected code in the text. Move to previous instance in text from
        the current textEdit cursor position.
        Called by: pushButton_show_codings_previous
        """

        if self.file_ is None:
            return
        item = self.ui.treeWidget.currentItem()
        if item is None or item.text(1)[0:3] == 'cat':
            return
        cid = int(item.text(1)[4:])
        # Index list has to be dynamic, as a new code_text item could be created before this method is called again
        # Develop indexes and tooltip coded text list
        indexes = []
        tt_code_text = []
        for ct in self.code_text:
            if ct['cid'] == cid:
                indexes.append(ct)
                tt_code_text.append(ct)
        indexes = sorted(indexes, key=itemgetter('pos0'), reverse=True)
        cursor = self.ui.textEdit.textCursor()
        cur_pos = cursor.position()
        end_pos = 0
        found_smaller = False
        msg = f"/{len(indexes)}"
        for i, index in enumerate(indexes):
            if index['pos0'] - self.file_['start'] < cur_pos - 1:
                cur_pos = index['pos0'] - self.file_['start']
                end_pos = index['pos1'] - self.file_['start']
                found_smaller = True
                msg = str(len(indexes) - i) + msg
                break
        if not found_smaller and indexes == []:
            return
        # Loop around to the highest index
        if not found_smaller and indexes != []:
            cur_pos = indexes[0]['pos0'] - self.file_['start']
            end_pos = indexes[0]['pos1'] - self.file_['start']
            msg = str(len(indexes)) + msg
        msg += " " + _("Code:") + " " + msg
        self.unlight()
        # Highlight the code in the text
        color = ""
        for c in self.codes:
            if c['cid'] == cid:
                color = c['color']
        cursor.setPosition(cur_pos)
        self.ui.textEdit.setTextCursor(cursor)
        cursor.setPosition(cur_pos, QtGui.QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(end_pos, QtGui.QTextCursor.MoveMode.KeepAnchor)
        brush = QBrush(QColor(color))
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(brush)
        foreground_colour = TextColor(color).recommendation
        fmt.setForeground(QBrush(QColor(foreground_colour)))
        cursor.mergeCharFormat(fmt)
        # Update tooltips to show only this code
        self.eventFilterTT.set_codes_and_annotations(self.app, tt_code_text, self.codes, self.annotations,
                                                     self.file_)
        # Need to reload icons as they disapear on Windows
        self.ui.pushButton_show_all_codings.setIcon(qta.icon('mdi6.grid'))
        self.ui.pushButton_show_codings_prev.setStyleSheet(f"background-color: {color};color:{foreground_colour}")
        self.ui.pushButton_show_codings_prev.setIcon(qta.icon('mdi6.arrow-left'))
        tt = _("Show previous coding of selected code") + msg
        self.ui.pushButton_show_codings_prev.setToolTip(tt)
        self.ui.pushButton_show_codings_next.setStyleSheet(f"background-color: {color};color:{foreground_colour}")
        self.ui.pushButton_show_codings_next.setIcon(qta.icon('mdi6.arrow-right'))
        tt = _("Show next coding of selected code") + msg
        self.ui.pushButton_show_codings_next.setToolTip(tt)

    def show_all_codes_in_text(self):
        """ Opposes show selected code methods.
        Highlights all the codes in the text. """

        self.ui.pushButton_show_all_codings.setIcon(qta.icon('mdi6.grid-off'))
        self.ui.pushButton_show_codings_prev.setStyleSheet("")
        self.ui.pushButton_show_codings_next.setStyleSheet("")
        self.ui.pushButton_show_codings_prev.setIcon(qta.icon('mdi6.arrow-left'))
        tt = _("Show previous coding of selected code")
        self.ui.pushButton_show_codings_prev.setToolTip(tt)
        self.ui.pushButton_show_codings_next.setIcon(qta.icon('mdi6.arrow-right'))
        tt = _("Show next coding of selected code")
        self.ui.pushButton_show_codings_next.setToolTip(tt)
        self.get_coded_text_update_eventfilter_tooltips()

    def coded_media_dialog(self, code_dict):
        """ Display all coded media for this code, in a separate modal dialog.
        Coded media comes from ALL files for this coder.
        Need to store textedit start and end positions so that code in context can be used.
        Called from tree_menu.
        Re-load coded text as codes may have changed.
        Args:
            code_dict : code dictionary
        """

        DialogCodeInAllFiles(self.app, code_dict)
        self.get_coded_text_update_eventfilter_tooltips()

    def item_moved_update_data(self, item, parent):
        """ Called from drop event in treeWidget view port.
        identify code or category to move.
        Also merge codes if one code is dropped on another code.
        Args:
            item : QTreeWidgetItem
            parent : QTreeWidgetItem
        """

        # Find the category in the list
        if item.text(1)[0:3] == 'cat':
            found = -1
            for i in range(0, len(self.categories)):
                if self.categories[i]['catid'] == int(item.text(1)[6:]):
                    found = i
            if found == -1:
                return
            if parent is None:
                self.categories[found]['supercatid'] = None
            else:
                if parent.text(1).split(':')[0] == 'cid':
                    # Parent is code (leaf) cannot add child
                    return
                supercatid = int(parent.text(1).split(':')[1])
                if supercatid == self.categories[found]['catid']:
                    # Something went wrong
                    return
                self.categories[found]['supercatid'] = supercatid
            cur = self.app.conn.cursor()
            cur.execute("update code_cat set supercatid=? where catid=?",
                        [self.categories[found]['supercatid'], self.categories[found]['catid']])
            self.app.conn.commit()
            self.update_dialog_codes_and_categories()
            self.app.delete_backup = False
            return

        # find the code in the list
        if item.text(1)[0:3] == 'cid':
            found = -1
            for i in range(0, len(self.codes)):
                if self.codes[i]['cid'] == int(item.text(1)[4:]):
                    found = i
            if found == -1:
                return
            if parent is None:
                self.codes[found]['catid'] = None
            else:
                if parent.text(1).split(':')[0] == 'cid':
                    # Parent is code (leaf) cannot add child, but can merge
                    self.merge_codes(self.codes[found], parent)
                    return
                catid = int(parent.text(1).split(':')[1])
                self.codes[found]['catid'] = catid

            cur = self.app.conn.cursor()
            cur.execute("update code_name set catid=? where cid=?",
                        [self.codes[found]['catid'], self.codes[found]['cid']])
            self.app.conn.commit()
            self.app.delete_backup = False
            self.update_dialog_codes_and_categories()

    def merge_codes(self, item, parent):
        """ Merge code with another code.
        Called by item_moved_update_data when a code is moved onto another code.
        code text unique(cid,fid,pos0,pos1, owner)
        Args:
            item : Dictionary code item
            parent : QTreeWidgetItem
        """

        # Check item dropped on itself, an error can occur on Ubuntu 22.04.
        if item['name'] == parent.text(0):
            return
        msg = '<p style="font-size:' + str(self.app.settings['fontsize']) + 'px">'
        msg += _("Merge code: ") + item['name'] + _(" into code: ") + parent.text(0) + '</p>'
        reply = QtWidgets.QMessageBox.question(self, _('Merge codes'),
                                               msg, QtWidgets.QMessageBox.StandardButton.Yes,
                                               QtWidgets.QMessageBox.StandardButton.No)
        if reply == QtWidgets.QMessageBox.StandardButton.No:
            return
        cur = self.app.conn.cursor()
        old_cid = item['cid']
        new_cid = int(parent.text(1).split(':')[1])
        # Update cid for each coded segment in text, av, image. Delete where there is an Integrity error
        ct_sql = "select ctid from code_text where cid=?"
        cur.execute(ct_sql, [old_cid])
        ct_res = cur.fetchall()
        try:
            for ct in ct_res:
                try:
                    cur.execute("update code_text set cid=? where ctid=?", [new_cid, ct[0]])
                except sqlite3.IntegrityError:
                    cur.execute("delete from code_text where ctid=?", [ct[0]])
            av_sql = "select avid from code_av where cid=?"
            cur.execute(av_sql, [old_cid])
            av_res = cur.fetchall()
            for av in av_res:
                try:
                    cur.execute("update code_av set cid=? where avid=?", [new_cid, av[0]])
                except sqlite3.IntegrityError:
                    cur.execute("delete from code_av where avid=?", [av[0]])
            img_sql = "select imid from code_image where cid=?"
            cur.execute(img_sql, [old_cid])
            img_res = cur.fetchall()
            for img in img_res:
                try:
                    cur.execute("update code_image set cid=? where imid=?", [new_cid, img[0]])
                except sqlite3.IntegrityError:
                    cur.execute("delete from code_image where imid=?", [img[0]])
            cur.execute("delete from code_name where cid=?", [old_cid, ])
            self.app.conn.commit()
        except Exception as e_:
            print(e_)
            self.app.conn.rollback()  # Revert all changes
            raise
        self.app.delete_backup = False
        msg = msg.replace("\n", " ")
        self.parent_textEdit.append(msg)
        self.update_dialog_codes_and_categories()
        self.get_coded_text_update_eventfilter_tooltips()

    def add_code(self, catid=None, code_name=""):
        """ Use add_item dialog to get new code text. Add_code_name dialog checks for
        duplicate code name. A random color is selected for the code, or a color has been pre-set by the user.
        New code is added to data and database.
        Args:
            catid : None to add code without category, catid Integer to add to category.
            code_name : String : Used for 'in vivo' coding where name is preset by in vivo text selection.
        Returns:
            True  - new code added, False - code exists or could not be added
        """

        if code_name == "":
            ui = DialogAddItemName(self.app, self.codes, _("Add new code"), _("Code name"))
            ui.exec()
            code_name = ui.get_new_name()
            if code_name is None:
                return False
        code_color = colors[randint(0, len(colors) - 1)]
        if self.default_new_code_color:
            code_color = self.default_new_code_color
        item = {'name': code_name, 'memo': "", 'owner': self.app.settings['codername'],
                'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"), 'catid': catid,
                'color': code_color}
        cur = self.app.conn.cursor()
        try:
            cur.execute("insert into code_name (name,memo,owner,date,catid,color) values(?,?,?,?,?,?)",
                        (item['name'], item['memo'], item['owner'], item['date'], item['catid'], item['color']))
            self.app.conn.commit()
            self.app.delete_backup = False
            cur.execute("select last_insert_rowid()")
            cid = cur.fetchone()[0]
            item['cid'] = cid
            self.parent_textEdit.append(_("New code: ") + item['name'])
        except sqlite3.IntegrityError:
            # Can occur with in vivo coding
            print("in vivo coding. Code already exists")
            return False
        self.update_dialog_codes_and_categories()
        self.get_coded_text_update_eventfilter_tooltips()
        return True

    def update_dialog_codes_and_categories(self):
        """ Update code and category tree here and in DialogReportCodes, ReportCoderComparisons, ReportCodeFrequencies
        Using try except blocks for each instance, as instance may have been deleted. """

        self.get_codes_and_categories()
        self.fill_tree()
        self.unlight()
        self.highlight()
        self.get_coded_text_update_eventfilter_tooltips()

        contents = self.tab_reports.layout()
        if contents:
            for i in reversed(range(contents.count())):
                c = contents.itemAt(i).widget()
                if isinstance(c, DialogReportCodes):
                    c.get_codes_categories_coders()
                    c.fill_tree()
                if isinstance(c, DialogReportCoderComparisons):
                    c.get_data()
                    c.fill_tree()
                if isinstance(c, DialogReportCodeFrequencies):
                    c.get_data()
                    c.fill_tree()
                if isinstance(c, DialogReportCodeSummary):
                    c.get_codes_and_categories()
                    c.fill_tree()

    def add_category(self, supercatid=None):
        """ When button pressed, add a new category.
        Note: the addItem dialog does the checking for duplicate category names
        Args:
            supercatid : None to add without category, supercatid to add to category. """

        ui = DialogAddItemName(self.app, self.categories, _("Category"), _("Category name"))
        ui.exec()
        new_category_name = ui.get_new_name()
        if new_category_name is None:
            return
        item = {'name': new_category_name, 'cid': None, 'memo': "",
                'owner': self.app.settings['codername'],
                'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")}
        cur = self.app.conn.cursor()
        cur.execute("insert into code_cat (name, memo, owner, date, supercatid) values(?,?,?,?,?)",
                    (item['name'], item['memo'], item['owner'], item['date'], supercatid))
        self.app.conn.commit()
        self.update_dialog_codes_and_categories()
        self.app.delete_backup = False
        self.parent_textEdit.append(_("New category: ") + item['name'])

    def delete_category_or_code(self, selected):
        """ Determine if selected item is a code or category before deletion.
        Args:
            selected: QTreeWidgetItem
        """

        if selected.text(1)[0:3] == 'cat':
            self.delete_category(selected)
            return  # Avoid error as selected is now None
        if selected.text(1)[0:3] == 'cid':
            self.delete_code(selected)

    def delete_code(self, selected):
        """ Find code, remove from database, refresh and code data and fill treeWidget.
        Args:
            selected: QTreeWidgetItem
        """

        # Find the code in the list, check to delete
        found = -1
        for i in range(0, len(self.codes)):
            if self.codes[i]['cid'] == int(selected.text(1)[4:]):
                found = i
        if found == -1:
            return
        code_ = self.codes[found]
        ui = DialogConfirmDelete(self.app, _("Code: ") + selected.text(0))
        ok = ui.exec()
        if not ok:
            return
        cur = self.app.conn.cursor()
        cur.execute("delete from code_name where cid=?", [code_['cid'], ])
        cur.execute("delete from code_text where cid=?", [code_['cid'], ])
        cur.execute("delete from code_av where cid=?", [code_['cid'], ])
        cur.execute("delete from code_image where cid=?", [code_['cid'], ])
        self.app.conn.commit()
        self.app.delete_backup = False
        self.update_dialog_codes_and_categories()
        self.parent_textEdit.append(_("Code deleted: ") + code_['name'] + "\n")
        # Remove from recent codes
        for item in self.recent_codes:
            if item['name'] == code_['name']:
                self.recent_codes.remove(item)
                break
        self.update_dialog_codes_and_categories()
        self.app.delete_backup = False

    def delete_category(self, selected):
        """ Find category, remove from database, refresh categories and code data
        and fill treeWidget.
        Args:
            selected: QTreeWidgetItem
        """

        found = -1
        for i in range(0, len(self.categories)):
            if self.categories[i]['catid'] == int(selected.text(1)[6:]):
                found = i
        if found == -1:
            return
        category = self.categories[found]
        ui = DialogConfirmDelete(self.app, _("Category: ") + selected.text(0))
        ok = ui.exec()
        if not ok:
            return
        cur = self.app.conn.cursor()
        cur.execute("update code_name set catid=null where catid=?", [category['catid'], ])
        cur.execute("update code_cat set supercatid=null where catid = ?", [category['catid'], ])
        cur.execute("delete from code_cat where catid = ?", [category['catid'], ])
        self.app.conn.commit()
        self.update_dialog_codes_and_categories()
        self.app.delete_backup = False
        self.parent_textEdit.append(_("Category deleted: ") + category['name'])

    def add_edit_cat_or_code_memo(self, selected):
        """ View and edit a memo for a category or code.
        Args:
            selected: QTreeWidgetItem
        """

        if selected.text(1)[0:3] == 'cid':
            # Find the code in the list
            found = -1
            for i in range(0, len(self.codes)):
                if self.codes[i]['cid'] == int(selected.text(1)[4:]):
                    found = i
            if found == -1:
                return
            ui = DialogMemo(self.app, _("Memo for Code: ") + self.codes[found]['name'], self.codes[found]['memo'])
            ui.exec()
            memo = ui.memo
            if memo != self.codes[found]['memo']:
                self.codes[found]['memo'] = memo
                cur = self.app.conn.cursor()
                cur.execute("update code_name set memo=? where cid=?", (memo, self.codes[found]['cid']))
                self.app.conn.commit()
                self.app.delete_backup = False
            if memo == "":
                selected.setData(2, QtCore.Qt.ItemDataRole.DisplayRole, "")
            else:
                selected.setData(2, QtCore.Qt.ItemDataRole.DisplayRole, _("Memo"))
                self.parent_textEdit.append(_("Memo for code: ") + self.codes[found]['name'])

        if selected.text(1)[0:3] == 'cat':
            # Find the category in the list
            found = -1
            for i in range(0, len(self.categories)):
                if self.categories[i]['catid'] == int(selected.text(1)[6:]):
                    found = i
            if found == -1:
                return
            ui = DialogMemo(self.app, _("Memo for Category: ") + self.categories[found]['name'],
                            self.categories[found]['memo'])
            ui.exec()
            memo = ui.memo
            if memo != self.categories[found]['memo']:
                self.categories[found]['memo'] = memo
                cur = self.app.conn.cursor()
                cur.execute("update code_cat set memo=? where catid=?", (memo, self.categories[found]['catid']))
                self.app.conn.commit()
                self.app.delete_backup = False
            if memo == "":
                selected.setData(2, QtCore.Qt.ItemDataRole.DisplayRole, "")
            else:
                selected.setData(2, QtCore.Qt.ItemDataRole.DisplayRole, _("Memo"))
                self.parent_textEdit.append(_("Memo for category: ") + self.categories[found]['name'])
        self.update_dialog_codes_and_categories()

    def rename_category_or_code(self, selected):
        """ Rename a code or category.
        Check that the code or category name is not currently in use.
        Args:
            selected : QTreeWidgetItem """

        if selected.text(1)[0:3] == 'cid':
            code_ = None
            for c in self.codes:
                if c['cid'] == int(selected.text(1)[4:]):
                    code_ = c
            new_name, ok = QtWidgets.QInputDialog.getText(self, _("Rename code"),
                                                          _("New code name:") + " " * 40,
                                                          QtWidgets.QLineEdit.EchoMode.Normal,
                                                          code_['name'])
            if not ok or new_name == '':
                return
            # Check that no other code has this name
            for c in self.codes:
                if c['name'] == new_name:
                    Message(self.app, _("Name in use"),
                            new_name + _(" is already in use, choose another name."), "warning").exec()
                    return
            # Find the code in the list
            found = -1
            for i in range(0, len(self.codes)):
                if self.codes[i]['cid'] == int(selected.text(1)[4:]):
                    found = i
            if found == -1:
                return
            # Rename in recent codes
            for item in self.recent_codes:
                if item['name'] == self.codes[found]['name']:
                    item['name'] = new_name
                    break
            # Update codes list and database
            cur = self.app.conn.cursor()
            cur.execute("update code_name set name=? where cid=?", (new_name, self.codes[found]['cid']))
            self.app.conn.commit()
            self.app.delete_backup = False
            old_name = self.codes[found]['name']
            self.parent_textEdit.append(_("Code renamed from: ") + old_name + _(" to: ") + new_name)
            self.update_dialog_codes_and_categories()
            return

        if selected.text(1)[0:3] == 'cat':
            cat = None
            for c in self.categories:
                if c['catid'] == int(selected.text(1)[6:]):
                    cat = c
            new_name, ok = QtWidgets.QInputDialog.getText(self, _("Rename category"),
                                                          _("New category name:") + " " * 40,
                                                          QtWidgets.QLineEdit.EchoMode.Normal, cat['name'])
            if not ok or new_name == '':
                return
            # Check that no other category has this name
            for c in self.categories:
                if c['name'] == new_name:
                    msg = _("This code name is already in use.")
                    Message(self.app, _("Duplicate code name"), msg, "warning").exec()
                    return
            # Find the category in the list
            found = -1
            for i in range(0, len(self.categories)):
                if self.categories[i]['catid'] == int(selected.text(1)[6:]):
                    found = i
            if found == -1:
                return
            # Update category list and database
            cur = self.app.conn.cursor()
            cur.execute("update code_cat set name=? where catid=?",
                        (new_name, self.categories[found]['catid']))
            self.app.conn.commit()
            self.app.delete_backup = False
            old_name = self.categories[found]['name']
            self.update_dialog_codes_and_categories()
            self.parent_textEdit.append(_("Category renamed from: ") + old_name + _(" to: ") + new_name)

    def change_code_color(self, selected):
        """ Change the colour of the currently selected code.
        Args:
            selected : QTreeWidgetItem """

        cid = int(selected.text(1)[4:])
        found = -1
        for i in range(0, len(self.codes)):
            if self.codes[i]['cid'] == cid:
                found = i
        if found == -1:
            return
        ui = DialogColorSelect(self.app, self.codes[found])
        ok = ui.exec()
        if not ok:
            return
        new_color = ui.get_color()
        if new_color is None:
            return
        selected.setBackground(0, QBrush(QColor(new_color), Qt.BrushStyle.SolidPattern))
        # Update codes list, database and color markings
        self.codes[found]['color'] = new_color
        cur = self.app.conn.cursor()
        cur.execute("update code_name set color=? where cid=?",
                    (self.codes[found]['color'], self.codes[found]['cid']))
        self.app.conn.commit()
        self.app.delete_backup = False
        self.update_dialog_codes_and_categories()

    def file_menu(self, position):
        """ Context menu for listWidget files to get to the next file and
        to go to the file with the latest codings by this coder.
        Each file dictionary item in self.filenames contains:
        {'id', 'name', 'memo', 'characters'= number of characters in the file,
        'start' = showing characters from this position, 'end' = showing characters to this position}

        Args:
            position : """

        selected = self.ui.listWidget.currentItem()
        if selected is None:
            return
        file_ = None
        for f in self.filenames:
            if selected.text() == f['name']:
                file_ = f
        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        action_next = None
        action_latest = None
        action_next_chars = None
        action_prev_chars = None
        action_show_files_like = None
        action_show_case_files = None
        action_show_by_attribute = None
        action_memo = menu.addAction(_("Open memo"))
        action_view_original_text = None
        if file_ is not None and file_['mediapath'] is not None and len(file_['mediapath']) > 6 and \
                (file_['mediapath'][:6] == '/docs/' or file_['mediapath'][:5] == 'docs:'):
            action_view_original_text = menu.addAction(_("view original text file"))
        if len(self.app.get_text_filenames()) > 1:
            if len(self.filenames) != 1:
                action_next = menu.addAction(_("Next file"))
            action_latest = menu.addAction(_("File with latest coding"))
            action_show_files_like = menu.addAction(_("Show files like"))
            action_show_by_attribute = menu.addAction(_("Show files by attributes"))
            action_show_case_files = menu.addAction(_("Show case files"))
        if file_['characters'] > self.app.settings['codetext_chunksize']:
            action_next_chars = menu.addAction(str(self.app.settings['codetext_chunksize']) + _(" next  characters"))
            if file_['start'] > 0:
                action_prev_chars = menu.addAction(
                    str(self.app.settings['codetext_chunksize']) + _(" previous  characters"))
        action_go_to_bookmark = menu.addAction(_("Go to bookmark"))
        action = menu.exec(self.ui.listWidget.mapToGlobal(position))
        if action is None:
            return
        if action == action_memo:
            self.file_memo(file_)
        if action == action_view_original_text:
            self.view_original_text_file()
        if action == action_next:
            self.go_to_next_file()
        if action == action_latest:
            self.go_to_latest_coded_file()
        if action == action_go_to_bookmark:
            self.go_to_bookmark()
        if action == action_next_chars:
            self.next_chars(file_, selected)
        if action == action_prev_chars:
            self.prev_chars(file_, selected)
        if action == action_show_files_like:
            self.show_files_like()
        if action == action_show_case_files:
            self.show_case_files()
        if action == action_show_by_attribute:
            self.get_files_from_attributes()

    def view_original_text_file(self):
        """ View original text file.
         param:
         mediapath: String '/docs/' for internal 'docs:/' for external """

        if self.file_['mediapath'][:6] == "/docs/":
            doc_path = self.app.project_path + "/documents/" + self.file_['mediapath'][6:]
            webbrowser.open(doc_path)
            return
        if self.file_['mediapath'][:5] == "docs:":
            doc_path = self.file_['mediapath'][5:]
            webbrowser.open(doc_path)
            return
        logger.error("Cannot open text file in browser " + self.file_['mediapath'])
        print("code_text.view_original_text_file. Cannot open text file in browser " + self.file_['mediapath'])

    def show_case_files(self):
        """ Show files of specified case.
        Or show all files. """

        cases = self.app.get_casenames()
        cases.insert(0, {"name": _("Show all files"), "id": -1})
        ui = DialogSelectItems(self.app, cases, _("Select case"), "single")
        ok = ui.exec()
        if not ok:
            return
        selection = ui.get_selected()
        if not selection:
            return
        if selection['id'] == -1:
            self.get_files()
            return
        cur = self.app.conn.cursor()
        cur.execute('select fid from case_text where caseid=?', [selection['id']])
        res = cur.fetchall()
        file_ids = [r[0] for r in res]
        self.get_files(file_ids)

    def show_files_like(self):
        """ Show files that contain specified filename text.
        If blank, show all files. """

        dialog = QtWidgets.QInputDialog(self)
        dialog.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog.setWindowTitle(_("Show files like"))
        dialog.setWindowFlags(dialog.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog.setLabelText(_("Show files containing the text. (Blank for all)"))
        dialog.resize(200, 20)
        ok = dialog.exec()
        if not ok:
            return
        text_ = str(dialog.textValue())
        if text_ == "":
            self.get_files()
            return
        cur = self.app.conn.cursor()
        cur.execute('select id from source where name like ?', ['%' + text_ + '%'])
        res = cur.fetchall()
        file_ids = [r[0] for r in res]
        self.get_files(file_ids)

    def prev_chars(self, file_, selected):
        """ Load previous text chunk of the text file.
        params:
            file_  : selected file, Dictionary
            selected:  list widget item """

        # Already at start
        if file_['start'] == 0:
            return
        file_['end'] = file_['start']
        file_['start'] = file_['start'] - self.app.settings['codetext_chunksize']
        # Forward track to the first line ending for a better start of text chunk
        line_ending = False
        i = 0
        try:
            while file_['start'] + i < file_['end'] and not line_ending:
                if file_['fulltext'][file_[
                                         'start'] + i - 1] == "\n":  # ... + i - 1] because we want to include the line break in the chunk, and text[start:i] would otherwise exclude it
                    line_ending = True
                else:
                    i += 1
        except IndexError:
            pass
        file_['start'] += i
        # Check displayed text not going before start of characters
        if file_['start'] < 0:
            file_['start'] = 0
        # Update tooltip for listItem
        tt = selected.toolTip()
        tt2 = tt.split("From: ")[0]
        tt2 += "\n" + _("From: ") + str(file_['start']) + _(" to ") + str(file_['end'])
        selected.setToolTip(tt2)
        # Load file section into textEdit
        self.load_file(file_)

    def next_chars(self, file_, selected):
        """ Load next text chunk of the text file.
        params:
            file_  : selected file, Dictionary
            selected:  list widget item """

        # First time
        if file_['start'] == 0 and file_['end'] == file_['characters']:
            # Backtrack to the first line ending for a better end of text chunk
            i = self.app.settings['codetext_chunksize']
            line_ending = False
            while i > 0 and not line_ending:
                if file_['fulltext'][
                    i - 1] == "\n":  # [i - 1] because we want to include the line break in the chunk, and text[start:i] would otherwise exclude it
                    line_ending = True
                else:
                    i -= 1
            if i <= 0:
                file_['end'] = self.app.settings['codetext_chunksize']
            else:
                file_['end'] = i
        else:
            file_['start'] = file_['start'] + self.app.settings['codetext_chunksize']
            # Backtrack from start to next line ending for a better start of text chunk
            line_ending = False
            try:
                while file_['start'] > 0 and not line_ending:
                    if file_['fulltext'][file_['start'] - 1] == "\n":
                        line_ending = True
                    else:
                        file_['start'] -= 1
            except IndexError:
                pass
            # Backtrack from end to next line ending for a better end of text chunk
            i = self.app.settings['codetext_chunksize']
            if file_['start'] + i >= file_['characters']:
                i = file_['characters'] - file_['start'] - 1  # To prevent Index out of range error
            line_ending = False
            while i > 0 and not line_ending:
                if file_['fulltext'][file_['start'] + i - 1] == "\n":
                    line_ending = True
                else:
                    i -= 1
            file_['end'] = file_['start'] + i
            # Check displayed text going past end of characters
            if file_['end'] >= file_['characters']:
                file_['end'] = file_['characters'] - 1

        # Update tooltip for listItem
        tt = selected.toolTip()
        tt2 = tt.split("From: ")[0]
        tt2 += "\n" + _("From: ") + str(file_['start']) + _(" to ") + str(file_['end'])
        selected.setToolTip(tt2)
        # Load file section into textEdit
        self.load_file(file_)

    def go_to_next_file(self):
        """ Go to next file in list. Button. """

        if self.file_ is None:
            self.load_file(self.filenames[0])
            self.ui.listWidget.setCurrentRow(0)
            return
        for i in range(0, len(self.filenames) - 1):
            if self.file_ == self.filenames[i]:
                found = self.filenames[i + 1]
                self.ui.listWidget.setCurrentRow(i + 1)
                self.load_file(found)
                self.search_term = ""
                return

    def go_to_latest_coded_file(self):
        """ Go and open file with the latest coding. Button. """

        sql = "SELECT fid FROM code_text where owner=? order by date desc limit 1"
        cur = self.app.conn.cursor()
        cur.execute(sql, [self.app.settings['codername'], ])
        result = cur.fetchone()
        if result is None:
            return
        for i, f in enumerate(self.filenames):
            if f['id'] == result[0]:
                self.ui.listWidget.setCurrentRow(i)
                self.load_file(f)
                self.search_term = ""
                break

    def go_to_bookmark(self):
        """ Find bookmark, open the file and highlight the bookmarked character.
        Adjust for start of text file, as this may be a smaller portion of the full text file.

        The currently loaded text portion may not contain the bookmark.
        Solution - reset the file start and end marks to the entire file length and
        load the entire text file.
        """

        cur = self.app.conn.cursor()
        cur.execute("select bookmarkfile, bookmarkpos from project")
        result = cur.fetchone()
        for i, f in enumerate(self.filenames):
            if f['id'] == result[0]:
                f['start'] = 0
                if f['end'] != f['characters']:
                    msg = _("Entire text file will be loaded")
                    Message(self.app, _('Information'), msg).exec()
                f['end'] = f['characters']
                try:
                    self.ui.listWidget.setCurrentRow(i)
                    self.load_file(f)
                    self.search_term = ""
                    # Set text cursor position and also highlight one character, to show location.
                    text_cursor = self.ui.textEdit.textCursor()
                    text_cursor.setPosition(result[1])
                    endpos = result[1] - 1
                    if endpos < 0:
                        endpos = 0
                    text_cursor.setPosition(endpos, QtGui.QTextCursor.MoveMode.KeepAnchor)
                    self.ui.textEdit.setTextCursor(text_cursor)
                except Exception as e:
                    logger.debug(str(e))
                break

    def listwidgetitem_view_file(self):
        """ When listwidget item is pressed load the file.
        The selected file is then displayed for coding.
        Note: file segment is also loaded from listWidget context menu """

        if len(self.filenames) == 0:
            return
        item_name = self.ui.listWidget.currentItem().text()
        for f in self.filenames:
            if f['name'] == item_name:
                self.file_ = f
                self.load_file(self.file_)
                self.search_term = ""
                break

    def file_selection_changed(self):
        """ File selection changed. """

        row = self.ui.listWidget.currentRow()
        self.load_file(self.filenames[row])

    def load_file(self, file_):
        """ Load and display file text for this file.
        Set the file as a selected item in the list widget. (due to the search text function searching across files).
        Get and display coding highlights.

        Called from:
            view_file_dialog, context_menu
        param: file_ : dictionary of name, id, memo, characters, start, end, fulltext
        """

        if file_ is None:
            return
        self.ui.listWidget.blockSignals(True)
        for x in range(self.ui.listWidget.count()):
            if self.ui.listWidget.item(x).text() == file_['name']:
                self.ui.listWidget.setCurrentRow(x)
                break
        self.ui.listWidget.blockSignals(False)
        self.file_ = file_
        if "start" not in self.file_:
            self.file_['start'] = 0
        sql_values = []
        try:
            file_result = self.app.get_file_texts([file_['id']])[0]
        except IndexError:
            # Error occurs when file opened here but also deleted in ManageFiles
            self.file_ = None
            return
        if "end" not in self.file_:
            self.file_['end'] = len(file_result['fulltext'])
        sql_values.append(int(file_result['id']))
        # Determine start line
        if self.file_['start'] == 0:
            self.file_['start_line'] = 1
        else:
            text_before = file_result['fulltext'][0:self.file_['start']]
            lines = text_before.splitlines()
            self.file_['start_line'] = len(lines) + 1
        self.number_bar.set_first_line(self.file_['start_line'], do_update=False)
        self.text = file_result['fulltext'][self.file_['start']:self.file_['end']]
        if self.text.endswith('\n'):
            self.text = self.text[
                        :-1]  # having '\n' at the end of the text sometimes creates an empty line in QTextEdit, so we omit it

        if self.file_['name'][-3:].lower() == ".md":
            highlighter = MarkdownHighlighter(self.ui.textEdit, self.app)
        else:
            highlighter = MarkdownHighlighter(self.ui.textEdit, self.app)
            highlighter.highlighting_rules = []

        self.ui.textEdit.setPlainText(self.text)
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()
        self.show_all_codes_in_text()  # Deactivates the show_selected_code if this is active
        self.setWindowTitle(_("Code text: ") + self.file_['name'])
        self.ui.lineEdit_search.setEnabled(True)
        self.ui.checkBox_search_case.setEnabled(True)
        self.ui.checkBox_search_all_files.setEnabled(True)
        # self.search_for_text()

    def get_coded_text_update_eventfilter_tooltips(self):
        """ Called by load_file, and from other dialogs on update.
        Tooltips are for all coded_text or only for important if important is flagged.
        """

        if self.file_ is None:
            return
        sql_values = [int(self.file_['id']), self.app.settings['codername'], self.file_['start'], self.file_['end']]
        # Get code text for this file and for this coder
        self.code_text = []
        # seltext length, longest first, so overlapping shorter text is superimposed.
        sql = "select code_text.ctid, code_text.cid, fid, seltext, pos0, pos1, code_text.owner, code_text.date, " \
              "code_text.memo, important, name"
        sql += " from code_text join code_name on code_text.cid = code_name.cid"
        sql += " where fid=? and code_text.owner=? "
        # For file text which is currently loaded
        sql += " and pos0 >=? and pos1 <=? "
        sql += "order by length(seltext) desc, important asc"
        cur = self.app.conn.cursor()
        cur.execute(sql, sql_values)
        code_results = cur.fetchall()
        keys = 'ctid', 'cid', 'fid', 'seltext', 'pos0', 'pos1', 'owner', 'date', 'memo', 'important', 'name'
        for row in code_results:
            self.code_text.append(dict(zip(keys, row)))
        # Update filter for tooltip and redo formatting
        if self.important:
            imp_coded = []
            for c in self.code_text:
                if c['important'] == 1:
                    imp_coded.append(c)
            self.eventFilterTT.set_codes_and_annotations(self.app, imp_coded, self.codes, self.annotations,
                                                         self.file_)
        else:
            self.eventFilterTT.set_codes_and_annotations(self.app, self.code_text, self.codes, self.annotations,
                                                         self.file_)
        self.unlight()
        self.highlight()

    def unlight(self):
        """ Remove all text highlighting from current file. """

        if self.text is None or self.text == "":
            return
        cursor = self.ui.textEdit.textCursor()
        cursor.setPosition(0, QtGui.QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(len(self.text) - 1, QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(QtGui.QTextCharFormat())

    def highlight(self):
        """ Apply text highlighting to current file.
        If no colour has been assigned to a code, those coded text fragments are coloured gray.
        Each code text item contains: fid, date, pos0, pos1, seltext, cid, status, memo,
        name, owner.
        For defined colours in color_selector, make text light on dark, and conversely dark on light
        """

        if self.file_ is None or self.ui.textEdit.toPlainText() == "":
            return
        # Add coding highlights
        codes = {x['cid']: x for x in self.codes}
        for item in self.code_text:
            fmt = QtGui.QTextCharFormat()
            cursor = self.ui.textEdit.textCursor()
            cursor.setPosition(int(item['pos0'] - self.file_['start']), QtGui.QTextCursor.MoveMode.MoveAnchor)
            cursor.setPosition(int(item['pos1'] - self.file_['start']), QtGui.QTextCursor.MoveMode.KeepAnchor)
            color = codes.get(item['cid'], {}).get('color', "#777777")  # default gray
            brush = QBrush(QColor(color))
            fmt.setBackground(brush)
            # Foreground depends on the defined need_white_text color in color_selector
            text_brush = QBrush(QColor(TextColor(color).recommendation))
            fmt.setForeground(text_brush)
            # Highlight codes with memos - these are italicised
            # Italics also used for overlapping codes
            if item['memo'] != "":
                fmt.setFontItalic(True)
            else:
                fmt.setFontItalic(False)
            # Bold important codes
            if item['important']:
                fmt.setFontWeight(QtGui.QFont.Weight.Bold)
            # Use important flag for ONLY showing important codes (button selected)
            if self.important and item['important'] == 1:
                cursor.setCharFormat(fmt)
            # Show all codes, as important button not selected
            if not self.important:
                cursor.setCharFormat(fmt)

        # Add annotation marks - these are in bold, important codings are also bold
        for note in self.annotations:
            if len(self.file_.keys()) > 0:  # will be zero if using autocode and no file is loaded
                # Cursor pos could be negative if annotation was for an earlier text portion
                cursor = self.ui.textEdit.textCursor()
                if note['fid'] == self.file_['id'] and \
                        0 <= int(note['pos0']) - self.file_['start'] < int(note['pos1']) - self.file_['start'] <= \
                        len(self.ui.textEdit.toPlainText()):
                    cursor.setPosition(int(note['pos0']) - self.file_['start'],
                                       QtGui.QTextCursor.MoveMode.MoveAnchor)
                    cursor.setPosition(int(note['pos1']) - self.file_['start'],
                                       QtGui.QTextCursor.MoveMode.KeepAnchor)
                    format_bold = QtGui.QTextCharFormat()
                    format_bold.setFontWeight(QtGui.QFont.Weight.Bold)
                    cursor.mergeCharFormat(format_bold)
        self.apply_underline_to_overlaps()

    def apply_underline_to_overlaps(self):
        """ Apply underline format to coded text sections which are overlapping.
        Qt underline options: # NoUnderline, SingleUnderline, DashUnderline, DotLine, DashDotLine, WaveUnderline
        Adjust for start of text file, as this may be a smaller portion of the full text file.
        """

        if self.important:
            return
        overlaps = []
        for i in self.code_text:
            for j in self.code_text:
                if j != i:
                    if j['pos0'] <= i['pos0'] <= j['pos1']:
                        if (j['pos0'] >= i['pos0'] and j['pos1'] <= i['pos1']) and (j['pos0'] != j['pos1']):
                            overlaps.append([j['pos0'], j['pos1']])
                        elif (i['pos0'] >= j['pos0'] and i['pos1'] <= j['pos1']) and (i['pos0'] != i['pos1']):
                            overlaps.append([i['pos0'], i['pos1']])
                        elif j['pos0'] > i['pos0'] and (j['pos0'] != i['pos1']):
                            overlaps.append([j['pos0'], i['pos1']])
                        elif j['pos1'] != i['pos0']:  # j['pos0'] < i['pos0']:
                            overlaps.append([j['pos1'], i['pos0']])
        cursor = self.ui.textEdit.textCursor()
        for o in overlaps:
            fmt = QtGui.QTextCharFormat()
            fmt.setUnderlineStyle(QtGui.QTextCharFormat.UnderlineStyle.SingleUnderline)
            if self.app.settings['stylesheet'] == 'dark':
                fmt.setUnderlineColor(QColor("#000000"))
            else:
                fmt.setUnderlineColor(QColor("#FFFFFF"))
            cursor.setPosition(o[0] - self.file_['start'], QtGui.QTextCursor.MoveMode.MoveAnchor)
            cursor.setPosition(o[1] - self.file_['start'], QtGui.QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(fmt)

    def mark(self):
        """ Mark selected text in file with currently selected code.
       Need to check for multiple same codes at same pos0 and pos1.
       Update recent_codes list.
       Adjust for start of text file, as this may be a smaller portion of the full text file.
       """

        if self.file_ is None:
            Message(self.app, _('Warning'), _("No file was selected"), "warning").exec()
            return
        self.clear_edit_variables()
        item = self.ui.treeWidget.currentItem()
        if item is None:
            Message(self.app, _('Warning'), _("No code was selected"), "warning").exec()
            return
        if item.text(1).split(':')[0] == 'catid':  # Cannot mark with category
            return
        cid = int(item.text(1).split(':')[1])
        selected_text = self.ui.textEdit.textCursor().selectedText()
        pos0 = self.ui.textEdit.textCursor().selectionStart() + self.file_['start']
        pos1 = self.ui.textEdit.textCursor().selectionEnd() + self.file_['start']
        if pos0 == pos1:
            return

        # Add the coded section to code text, add to database and update GUI
        coded = {'cid': cid, 'fid': int(self.file_['id']), 'seltext': selected_text,
                 'pos0': pos0, 'pos1': pos1, 'owner': self.app.settings['codername'], 'memo': "",
                 'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                 'important': None}

        # Check for an existing duplicated marking first
        cur = self.app.conn.cursor()
        cur.execute("select * from code_text where cid = ? and fid=? and pos0=? and pos1=? and owner=?",
                    (coded['cid'], coded['fid'], coded['pos0'], coded['pos1'], coded['owner']))
        result = cur.fetchall()
        if len(result) > 0:
            # The event can trigger multiple times, so do not present a warning to the user
            return
        self.code_text.append(coded)
        cur.execute("insert into code_text (cid,fid,seltext,pos0,pos1,owner,\
            memo,date, important) values(?,?,?,?,?,?,?,?,?)", (coded['cid'], coded['fid'],
                                                               coded['seltext'], coded['pos0'], coded['pos1'],
                                                               coded['owner'],
                                                               coded['memo'], coded['date'], coded['important']))
        self.app.conn.commit()
        self.app.delete_backup = False

        # Add AI interpretation?
        if self.ui.tabWidget.currentIndex() == 1:  # ai search
            ai_search_result = self.get_overlapping_ai_search_result()
            if ai_search_result is not None:
                memo = _("AI interpretation: ") + ai_search_result["interpretation"]
                memo += _("\n\nAI search prompt: ") + self.ai_search_prompt.name_and_scope()
                memo += _("\nAI model: ") + self.ai_search_ai_model

                msg = '<p style="font-size: ' + str(self.app.settings['fontsize']) + 'pt">'
                msg += _("Do you want to store the AI interpretation in a memo together with the coding?<br/><br/>")
                msg += '<i>' + memo.replace('\n', '<br/>') + '</i></p>'
                reply = QtWidgets.QMessageBox.question(
                    self, 'AI Interpretation', msg,
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.Yes
                )
                if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                    # Dictionary with cid fid seltext owner date name color memo
                    cur = self.app.conn.cursor()
                    cur.execute(
                        "update code_text set memo=? where cid=? and fid=? and seltext=? and pos0=? and pos1=? and owner=?",
                        (memo, coded['cid'], coded['fid'], coded['seltext'], coded['pos0'],
                         coded['pos1'],
                         coded['owner']))
                    self.app.conn.commit()
                    self.code_text[len(self.code_text) - 1]['memo'] = memo

        # Update filter for tooltip and update code colours
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()
        # Update recent_codes
        tmp_code = None
        for c in self.codes:
            if c['cid'] == cid:
                tmp_code = c
        if tmp_code is None:
            return
        # Need to remove from recent_codes, if there and add back in first position, and update project recently_used_codes
        for item in self.recent_codes:
            if item == tmp_code:
                self.recent_codes.remove(item)
                break
        self.recent_codes.insert(0, tmp_code)
        if len(self.recent_codes) > 10:
            self.recent_codes = self.recent_codes[:10]
        recent_codes_string = ""
        for r in self.recent_codes:
            recent_codes_string += f" {r['cid']}"
        recent_codes_string = recent_codes_string[1:]
        cur.execute("update project set recently_used_codes=?", [recent_codes_string])
        self.app.conn.commit()

        self.update_file_tooltip()  # Number of codes applied

    def undo_last_unmarked_code(self):
        """ Restore the last deleted code(s).
        One code or multiple, depends on what was selected when the unmark method was used.
        Requires self.undo_deleted_codes """

        if not self.undo_deleted_codes:
            return
        self.clear_edit_variables()
        cur = self.app.conn.cursor()
        for item in self.undo_deleted_codes:
            cur.execute("insert into code_text (cid,fid,seltext,pos0,pos1,owner,\
                memo,date, important) values(?,?,?,?,?,?,?,?,?)", (item['cid'], item['fid'],
                                                                   item['seltext'], item['pos0'], item['pos1'],
                                                                   item['owner'],
                                                                   item['memo'], item['date'], item['important']))
        self.app.conn.commit()
        self.undo_deleted_codes = []
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()

    def unmark(self, location):
        """ Remove code marking by this coder from selected text in current file.
        Called by text_edit_context_menu
        Adjust for start of text file, as this may be a smaller portion of the full text file.

        param:
            location: text cursor location, Integer
        """

        if self.file_ is None:
            return
        self.clear_edit_variables()
        unmarked_list = []
        for item in self.code_text:
            if item['pos0'] <= location + self.file_['start'] <= item['pos1'] and \
                    item['owner'] == self.app.settings['codername']:
                unmarked_list.append(item)
        if not unmarked_list:
            return
        to_unmark = []
        if len(unmarked_list) == 1:
            to_unmark = [unmarked_list[0]]
        # Multiple codes to select from
        if len(unmarked_list) > 1:
            ui = DialogSelectItems(self.app, unmarked_list, _("Select code to unmark"), "multi")
            ok = ui.exec()
            if not ok:
                return
            to_unmark = ui.get_selected()
        if to_unmark is None:
            return
        self.undo_deleted_codes = deepcopy(to_unmark)
        # Delete from db, remove from coding and update highlights
        cur = self.app.conn.cursor()
        for item in to_unmark:
            cur.execute("delete from code_text where ctid=?", [item['ctid']])
            self.app.conn.commit()
        # Update filter for tooltip and update code colours
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()
        self.update_file_tooltip()
        self.app.delete_backup = False

    def annotate(self, cursor_pos=None):
        """ Add view, or remove an annotation for selected text.
        Annotation positions are displayed as bold text.
        Adjust for start of text file, as this may be a smaller portion of the full text file.

        Called via context menu, button
        """

        if self.file_ is None:
            Message(self.app, _('Warning'), _("No file was selected"), "warning").exec()
            return
        self.clear_edit_variables()
        pos0 = self.ui.textEdit.textCursor().selectionStart()
        pos1 = self.ui.textEdit.textCursor().selectionEnd()
        text_length = len(self.ui.textEdit.toPlainText())
        if pos0 >= text_length or pos1 > text_length:
            return
        item = None
        details = ""
        annotation = ""
        # Find annotation at this position for this file
        if cursor_pos is None:
            for note in self.annotations:
                if ((note['pos0'] <= pos0 + self.file_['start'] <= note['pos1']) or
                    (note['pos0'] <= pos1 + self.file_['start'] <= note['pos1'])) \
                        and note['fid'] == self.file_['id']:
                    item = note  # use existing annotation
                    details = item['owner'] + " " + item['date']
                    break
        if cursor_pos is not None:  # Try point position, if cursor is on an annotation, but no text selected
            for note in self.annotations:
                if cursor_pos + self.file_['start'] >= note['pos0'] and cursor_pos <= note['pos1'] + self.file_['start'] \
                        and note['fid'] == self.file_['id']:
                    item = note  # use existing annotation
                    details = item['owner'] + " " + item['date']
                    pos0 = cursor_pos
                    pos1 = cursor_pos
                    break
        # Exit this method if no text selected and there is no annotation at this position
        if pos0 == pos1 and item is None:
            return

        # Add new item to annotations, add to database and update GUI
        if item is None:
            item = {'fid': int(self.file_['id']), 'pos0': pos0 + self.file_['start'],
                    'pos1': pos1 + self.file_['start'],
                    'memo': str(annotation), 'owner': self.app.settings['codername'],
                    'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"), 'anid': -1}
            ui = DialogMemo(self.app, _("Annotation: ") + details, item['memo'])
            ui.exec()
            item['memo'] = ui.memo
            if item['memo'] != "":
                cur = self.app.conn.cursor()
                cur.execute("insert into annotation (fid,pos0, pos1,memo,owner,date) \
                    values(?,?,?,?,?,?)", (item['fid'], item['pos0'], item['pos1'],
                                           item['memo'], item['owner'], item['date']))
                self.app.conn.commit()
                self.app.delete_backup = False
                cur.execute("select last_insert_rowid()")
                anid = cur.fetchone()[0]
                item['anid'] = anid
                self.annotations.append(item)
                self.parent_textEdit.append(_("Annotation added at position: ")
                                            + str(item['pos0']) + "-" + str(item['pos1']) + _(" for: ") + self.file_[
                                                'name'])
                self.get_coded_text_update_eventfilter_tooltips()
            return

        # Edit existing annotation
        ui = DialogMemo(self.app, _("Annotation: ") + details, item['memo'])
        ui.exec()
        item['memo'] = ui.memo
        if item['memo'] != "":
            item['date'] = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            cur = self.app.conn.cursor()
            sql = "update annotation set memo=?, date=? where anid=?"
            cur.execute(sql, (item['memo'], item['date'], item['anid']))
            self.app.conn.commit()
            self.app.delete_backup = False

            self.annotations = self.app.get_annotations()
            self.get_coded_text_update_eventfilter_tooltips()
            return

        # If blank delete the annotation
        if item['memo'] == "":
            cur = self.app.conn.cursor()
            cur.execute("delete from annotation where pos0 = ?", (item['pos0'],))
            self.app.conn.commit()
            self.app.delete_backup = False
            self.annotations = self.app.get_annotations()
            self.parent_textEdit.append(_("Annotation removed from position ")
                                        + str(item['pos0']) + _(" for: ") + self.file_['name'])
        self.get_coded_text_update_eventfilter_tooltips()

    '''def button_autocode_sentences_this_file(self):
        """ Flag to autocode sentences in one file """

        self.auto_code_sentences("")

    def button_autocode_sentences_all_files(self):
        """ Flag to autocode sentences across all text files. """

        self.auto_code_sentences("all")'''

    def button_autocode_surround(self):
        """ Autocode with selected code using start and end marks.
         Uses selected files.
         Line ending text representation \\n is replaced with the actual line ending character.
         Activated by: self.ui.pushButton_auto_code_surround
         Regex is not used for this function
         """

        self.clear_edit_variables()
        item = self.ui.treeWidget.currentItem()
        if item is None or item.text(1)[0:3] == 'cat':
            Message(self.app, _('Warning'), _("No code was selected"), "warning").exec()
            return
        ui = DialogGetStartAndEndMarks("Autocoding", "Autocoding surround")  # self.file_['name'], self.file_['name'])
        ok = ui.exec()
        if not ok:
            return
        start_mark = ui.get_start_mark()
        if "\\n" in start_mark:
            start_mark = start_mark.replace("\\n", "\n")
        end_mark = ui.get_end_mark()
        if "\\n" in end_mark:
            end_mark = end_mark.replace("\\n", "\n")
        if start_mark == "" or end_mark == "":
            Message(self.app, _('Warning'), _("Cannot have blank text marks"), "warning").exec()
            return

        ui = DialogSelectItems(self.app, self.filenames, _("Select files to code"), "many")
        ok = ui.exec()
        if not ok:
            return
        files = ui.get_selected()
        if len(files) == 0:
            return

        msg = _("Code text using start and end marks: ")  # + self.file_['name']
        msg += _("\nUsing ") + start_mark + _(" and ") + end_mark + "\n"
        cur = self.app.conn.cursor()
        cid = int(item.text(1)[4:])
        now_date = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

        # Find text chunks and insert coded into database
        already_assigned = 0
        entries = 0
        undo_list = []
        for f in files:
            text_starts = [match.start() for match in re.finditer(re.escape(start_mark), f['fulltext'])]
            text_ends = [match.start() for match in re.finditer(re.escape(end_mark), f['fulltext'])]
            try:
                for start_pos in text_starts:
                    # pos1 = -1  # Default if not found. Not Used
                    text_end_iterator = 0
                    try:
                        while start_pos >= text_ends[text_end_iterator]:
                            text_end_iterator += 1
                    except IndexError:
                        text_end_iterator = -1
                    if text_end_iterator >= 0:
                        pos1 = text_ends[text_end_iterator]
                        # Check if already coded in this file for this coder
                        sql = "select cid from code_text where cid=? and fid=? and pos0=? and pos1=? and owner=?"
                        cur.execute(sql, [cid, f['id'], start_pos, pos1, self.app.settings['codername']])
                        res = cur.fetchone()
                        if res is None:
                            seltext = f['fulltext'][start_pos: pos1]
                            sql = "insert into code_text (cid, fid, seltext, pos0, pos1, owner, date, memo) values(?,?,?,?,?,?,?,?)"
                            '''cur.execute(sql, (cid, self.file_['id'], seltext, start_pos, pos1,
                                              self.app.settings['codername'], now_date, ""))'''
                            cur.execute(sql, (cid, f['id'], seltext, start_pos, pos1,
                                              self.app.settings['codername'], now_date, ""))
                            # Add to undo auto-coding history
                            undo = {
                                "sql": "delete from code_text where cid=? and fid=? and pos0=? and pos1=? and owner=?",
                                "cid": cid, "fid": f['id'], "pos0": start_pos, "pos1": pos1,
                                "owner": self.app.settings['codername']
                                }
                            undo_list.append(undo)
                            entries += 1
                        else:
                            already_assigned += 1
                self.app.conn.commit()
            except Exception as e_:
                print(e_)
                self.app.conn.rollback()  # Revert all changes
                raise
        # Add to undo auto-coding history
        if len(undo_list) > 0:
            name = _("Coding using start and end marks") + _("\nCode: ") + item.text(0)
            name += _("\nWith start mark: ") + start_mark + _("\nEnd mark: ") + end_mark
            undo_dict = {"name": name, "sql_list": undo_list}
            self.autocode_history.insert(0, undo_dict)

        # Update filter for tooltip and update code colours
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()
        msg += str(entries) + _(" new coded sections found.") + "\n"
        if already_assigned > 0:
            msg += str(already_assigned) + " " + _("previously coded.") + "\n"
        self.parent_textEdit.append(msg)
        Message(self.app, "Autocode surround", msg).exec()
        self.app.delete_backup = False

    def undo_autocoding(self):
        """ Present a list of choices for the undo operation.
         Use selects and undoes the chosen autocoding operation.
         The autocode_history is a list of dictionaries with 'name' and 'sql_list' """

        if not self.autocode_history:
            return
        ui = DialogSelectItems(self.app, self.autocode_history, _("Select auto-codings to undo"), "single")
        ok = ui.exec()
        if not ok:
            return
        self.clear_edit_variables()
        undo = ui.get_selected()
        # Run all sqls
        cur = self.app.conn.cursor()
        try:
            for i in undo['sql_list']:
                cur.execute(i['sql'], [i['cid'], i['fid'], i['pos0'], i['pos1'], i['owner']])
            self.app.conn.commit()
        except Exception as e_:
            print(e_)
            self.app.conn.rollback()  # Revert all changes
            raise
        self.autocode_history.remove(undo)
        self.parent_textEdit.append(_("Undo autocoding: " + undo['name'] + "\n"))

        # Update filter for tooltip and update code colours
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()

    def auto_code_sentences(self):
        """ Code full sentence based on text fragment.
        Activated via self.ui.pushButton_auto_code_frag_this_file
        Opens a dialog to select text files for autocoding.
        """

        ui = DialogSelectItems(self.app, self.filenames, _("Select files to code"), "many")
        ok = ui.exec()
        if not ok:
            return
        files = ui.get_selected()
        if len(files) == 0:
            return
        item = self.ui.treeWidget.currentItem()
        if item is None or item.text(1)[0:3] == 'cat':
            Message(self.app, _('Warning'), _("No code was selected"), "warning").exec()
            return
        self.clear_edit_variables()
        cid = int(item.text(1).split(':')[1])
        dialog = QtWidgets.QInputDialog(None)
        dialog.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog.setWindowTitle(_("Code sentence"))
        dialog.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog.setLabelText(_("Auto code sentence using this text fragment:"))
        dialog.resize(200, 20)
        ok = dialog.exec()
        if not ok:
            return
        find_text = dialog.textValue()
        if find_text == "":
            return
        dialog_sentence_end = QtWidgets.QInputDialog(None)
        dialog_sentence_end.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog_sentence_end.setWindowTitle(_("Code sentence"))
        dialog_sentence_end.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog_sentence_end.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog_sentence_end.setToolTip("Use \\n for line ending")
        dialog_sentence_end.setLabelText(_("Define sentence ending. Default is period space.\nUse \\n for line ending:"))
        dialog_sentence_end.setTextValue(". ")
        dialog_sentence_end.resize(200, 40)
        ok2 = dialog_sentence_end.exec()
        if not ok2:
            return
        ending = dialog_sentence_end.textValue()
        if ending == "":
            return
        ending = ending.replace("\\n", "\n")

        cur = self.app.conn.cursor()
        msg = ""
        undo_list = []

        # Regex
        regex_pattern = None
        if self.ui.checkBox_auto_regex.isChecked():
            try:
                regex_pattern = re.compile(find_text)
            except re.error as e_:
                logger.warning('re error Bad escape ' + str(e_))
                Message(self.app, _("Regex compliation error"), str(e_))
            if regex_pattern is None:
                return

        try:
            for f in files:
                sentences = f['fulltext'].split(ending)
                pos0 = 0
                codes_added = 0
                for sentence in sentences:
                    if (find_text in sentence and not regex_pattern) or (regex_pattern and regex_pattern.search(sentence)):
                        i = {'cid': cid, 'fid': int(f['id']), 'seltext': str(sentence),
                             'pos0': pos0, 'pos1': pos0 + len(sentence),
                             'owner': self.app.settings['codername'], 'memo': "",
                             'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")}
                        # Possible IntegrityError: UNIQUE constraint failed
                        try:
                            codes_added += 1
                            cur.execute("insert into code_text (cid,fid,seltext,pos0,pos1,\
                                owner,memo,date) values(?,?,?,?,?,?,?,?)",
                                        (i['cid'], i['fid'], i['seltext'], i['pos0'],
                                         i['pos1'], i['owner'], i['memo'], i['date']))
                            # Record a list of undo sql
                            undo = {
                                "sql": "delete from code_text where cid=? and fid=? and pos0=? and pos1=? and owner=?",
                                "cid": i['cid'], "fid": i['fid'], "pos0": i['pos0'], "pos1": i['pos1'],
                                "owner": i['owner']
                            }
                            undo_list.append(undo)
                        except Exception as e:
                            print("Autocode insert error ", str(e))
                            logger.debug(_("Autocode insert error ") + str(e))
                    pos0 += len(sentence) + len(ending)
                if codes_added > 0:
                    msg += _("File: ") + f"{f['name']} {codes_added}" + _(" added codes") + "\n"
            self.app.conn.commit()
        except Exception as e_:
            print(e_)
            self.app.conn.rollback()  # revert all changes
            # undo_list = []
            raise
        if len(undo_list) > 0:
            name = _("Sentence coding: ") + _("\nCode: ") + item.text(0)
            name += _("\nWith: ") + find_text + _("\nUsing line ending: ") + ending
            undo_dict = {"name": name, "sql_list": undo_list}
            self.autocode_history.insert(0, undo_dict)
        self.parent_textEdit.append(_("Automatic code sentence in files:")
                                    + _("\nCode: ") + item.text(0)
                                    + _("\nWith text fragment: ")
                                    + find_text
                                    + _("\nUsing line ending: ")
                                    + ending + "\n" + msg)
        self.app.delete_backup = False
        # Update tooltip filter and code tree code counts
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()

    def auto_code(self):
        """ Autocode text in one file or all files with currently selected code.
        Button menu option to auto-code all, first or last instances in files.
        Split multiple find textx with pipe |
        Activated using self.ui.pushButton_auto_code
        """

        code_item = self.ui.treeWidget.currentItem()
        if code_item is None or code_item.text(1)[0:3] == 'cat':
            Message(self.app, _('Warning'), _("No code was selected"), "warning").exec()
            return
        cid = int(code_item.text(1).split(':')[1])
        # Input dialog too narrow, so code below to widen dialog
        dialog = QtWidgets.QInputDialog(None)
        dialog.setStyleSheet("* {font-size:" + str(self.app.settings['fontsize']) + "pt} ")
        dialog.setWindowTitle(_("Automatic coding"))
        dialog.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog.setToolTip(_("Use | to code multiple texts"))
        dialog.setLabelText(_("Auto code files with the current code for this text:") + "\n" + code_item.text(0))
        dialog.resize(200, 20)
        ok = dialog.exec()
        if not ok:
            return
        find_text = str(dialog.textValue())
        if find_text == "" or find_text is None:
            return
        texts_ = find_text.split('|')
        tmp = list(set(texts_))
        find_texts = []
        for t in tmp:
            if t != "":
                find_texts.append(t)
        # Regex, pipe | has different meaning, so do not split into separate texts
        if self.ui.checkBox_auto_regex.isChecked():
            find_texts = [find_text]
        if len(self.filenames) == 0:
            return
        ui = DialogSelectItems(self.app, self.filenames, _("Select files to code"), "many")
        ok = ui.exec()
        if not ok:
            return
        files = ui.get_selected()
        if len(files) == 0:
            return
        self.clear_edit_variables()

        # Regex
        regex_pattern = None
        if self.ui.checkBox_auto_regex.isChecked():
            try:
                regex_pattern = re.compile(find_texts[0])
            except re.error as e_:
                logger.warning('re error Bad escape ' + str(e_))
                Message(self.app, _("Regex compliation error"), str(e_))
            if regex_pattern is None:
                return

        undo_list = []
        cur = self.app.conn.cursor()
        try:
            for find_txt in find_texts:
                filenames = ""
                for f in files:
                    filenames += f['name'] + " "
                    cur.execute("select name, id, fulltext, memo, owner, date from source where id=? and "
                                "(mediapath is null or mediapath like '/docs/%' or mediapath like 'docs:%')",
                                [f['id']])
                    current_file = cur.fetchone()
                    # Rare but possible no result is returned.
                    if current_file is None:
                        continue

                    file_text = current_file[2]
                    text_starts = []
                    text_ends = []
                    if regex_pattern:
                        for match in regex_pattern.finditer(file_text):
                            text_starts.append(match.start())
                            text_ends.append(match.end())
                    else:
                        text_starts = [match.start() for match in re.finditer(re.escape(find_txt), file_text)]
                        text_ends = [match.end() for match in re.finditer(re.escape(find_txt), file_text)]

                    # Trim to first or last instance if option selected
                    if self.all_first_last == "first" and len(text_starts) > 1:
                        text_starts = [text_starts[0]]
                        text_ends = [text_ends[0]]
                    if self.all_first_last == "last" and len(text_starts) > 1:
                        text_starts = [text_starts[-1]]
                        text_ends = [text_ends[-1]]

                    # Add new items to database
                    for index in range(len(text_starts)):
                        item = {'cid': cid, 'fid': int(f['id']), 'seltext': str(find_txt),
                                'pos0': text_starts[index], 'pos1': text_ends[index],
                                'owner': self.app.settings['codername'], 'memo': "",
                                'date': datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")}
                        try:
                            cur.execute("insert into code_text (cid,fid,seltext,pos0,pos1,\
                                owner,memo,date) values(?,?,?,?,?,?,?,?)",
                                        [item['cid'], item['fid'], item['seltext'], item['pos0'],
                                         item['pos1'], item['owner'], item['memo'], item['date']])
                            # Record a list of undo sql
                            undo = {
                                "sql": "delete from code_text where cid=? and fid=? and pos0=? and pos1=? and owner=?",
                                "cid": item['cid'], "fid": item['fid'], "pos0": item['pos0'], "pos1": item['pos1'],
                                "owner": item['owner']}
                            undo_list.append(undo)
                        except sqlite3.IntegrityError as e:
                            logger.debug(_("Autocode insert error ") + str(e))
                        self.app.delete_backup = False
                self.app.conn.commit()
                self.parent_textEdit.append(_("Automatic coding in files: ") + filenames
                                            + _(". with text: ") + find_txt)
                if regex_pattern:
                    self.parent_textEdit.append(_("Using Regex."))
        except Exception as e_:
            print(e_)
            self.app.conn.rollback()  # Revert all changes
            # undo_list = []
            raise
        if len(undo_list) > 0:
            name = _("Text coding: ") + _("\nCode: ") + code_item.text(0)
            name += _("\nWith: ") + find_text
            undo_dict = {"name": name, "sql_list": undo_list}
            self.autocode_history.insert(0, undo_dict)
        # Update tooltip filter and code tree code counts
        self.get_coded_text_update_eventfilter_tooltips()
        self.fill_code_counts_in_tree()

    # Methods for Editing mode
    def undo_edited_text(self):
        """ Revert to the text prior to it being edited. """

        if self.edit_original_source is None:
            print("Should not occur")
            return
        cursor = self.app.conn.cursor()
        cursor.execute("update source set fulltext=? where id=?",
                       [self.edit_original_source, self.edit_original_source_id])
        # print("source id:", self.edit_original_source_id)
        # print("Source: ", self.edit_original_source)
        # print("Codes:", self.edit_original_codes)
        for c in self.edit_original_codes:
            cursor.execute("update code_text set seltext=?, pos0=?, pos1=? where ctid=?",
                           [c[1], c[2], c[3], c[0]])
        # print("annotes:", self.edit_original_annotations)
        for a in self.edit_original_annotations:
            cursor.execute("update annotation set pos0=?, pos1=? where anid=?",
                           [a[1], a[2], a[0]])
        # print("Case assignment", self.edit_original_case_assignment)
        for ca in self.edit_original_case_assignment:
            cursor.execute("update case_text set pos0=?, pos1=? where caseid=?",
                           [ca[1], ca[2], ca[0]])
        self.clear_edit_variables()
        self.ui.textEdit.installEventFilter(self.eventFilterTT)
        self.annotations = self.app.get_annotations()
        self.load_file(self.file_)
        self.update_file_tooltip()
        self.highlight()
        text_cursor = self.ui.textEdit.textCursor()
        if self.edit_pos > len(self.ui.textEdit.toPlainText()):
            self.edit_pos = len(self.ui.textEdit.toPlainText()) - 1
        text_cursor.setPosition(self.edit_pos, QtGui.QTextCursor.MoveMode.MoveAnchor)
        self.ui.textEdit.setTextCursor(text_cursor)
        msg = _("Text reverted to prior to edit")
        Message(self.app, _("Undo last edited text"), msg).exec()

    def clear_edit_variables(self):
        """ Called by undo pushbutton, or any coding, annotating, unmarking """

        self.edit_original_source = None
        self.edit_original_codes = None
        self.edit_original_annotations = None
        self.edit_original_case_assignment = None
        self.edit_original_cutoff_datetime = None
        self.edit_original_source_id = None
        self.ui.pushButton_undo_edit.setEnabled(False)

    def edit_mode_toggle(self):
        """ Activate or deactivate edit mode.
        When activated, hide most widgets, remove tooltips, remove text edit menu.
        Called: event filter Ctrl+E, or button press
        The edit mode toggle fires multiple times. so the initial edit_pos changes from the correct pos to 0
        """

        if self.file_ is None:
            return

        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self.edit_mode_on()
            self.ui.pushButton_undo_edit.setEnabled(True)
            return
        self.edit_mode_off()

    def edit_mode_on(self):
        """ Hide most widgets, remove tooltips, remove text edit menu.
        Need to load entire file, if only a section is currently loaded. """

        if self.file_ is None:
            return

        # Copy existing source and code_text codes and annotations and case text
        self.edit_original_source_id = self.file_['id']
        self.edit_original_annotations = []
        self.edit_original_codes = []
        self.edit_original_case_assignment = []
        self.edit_original_cutoff_datetime = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        cursor = self.app.conn.cursor()
        cursor.execute("select id, fulltext from source where id=?", [self.file_['id']])
        res_source = cursor.fetchone()
        self.edit_original_source = res_source[1]
        cursor.execute("select ctid, seltext,pos0,pos1 from code_text where fid=?", [self.file_['id']])
        res_codes = cursor.fetchall()
        if res_codes:
            self.edit_original_codes = res_codes
        cursor.execute("select anid, pos0,pos1 from annotation where fid=?", [self.file_['id']])
        res_annotations = cursor.fetchall()
        if res_annotations:
            self.edit_original_annotations = res_annotations
        cursor.execute("select id,pos0,pos1 from case_text where fid=?", [self.file_['id']])
        res_case = cursor.fetchall()
        if res_case:
            self.edit_original_case_assignment = res_case

        temp_edit_pos = self.ui.textEdit.textCursor().position() + self.file_['start']
        if temp_edit_pos > 0:
            self.edit_pos = temp_edit_pos
        self.ui.groupBox.hide()
        self.ui.groupBox_edit_mode.show()
        self.ui.listWidget.setEnabled(False)
        self.ui.widget_left.hide()
        self.ui.groupBox_file_buttons.setEnabled(False)
        self.ui.groupBox_file_buttons.setMaximumSize(4000, 4000)
        self.ui.groupBox_coding_buttons.setEnabled(False)
        self.ui.treeWidget.setEnabled(False)
        file_result = self.app.get_file_texts([self.file_['id']])[0]
        if self.file_['end'] != len(file_result['fulltext']) and self.file_['start'] != 0:
            self.file_['start'] = 0
            self.file_['end'] = len(file_result['fulltext'])
            self.text = file_result['fulltext']
            self.ui.textEdit.setText(self.text)
        self.prev_text = copy(self.text)
        self.ui.textEdit.removeEventFilter(self.eventFilterTT)
        self.get_cases_codings_annotations()
        self.ui.textEdit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextEditorInteraction |
            Qt.TextInteractionFlag.TextEditable
        )        
        self.ed_highlight()
        self.edit_mode_has_changed = False
        self.ui.textEdit.textChanged.connect(self.update_positions)
        text_cursor = self.ui.textEdit.textCursor()
        if self.edit_pos >= len(self.text):
            self.edit_pos = len(self.text) - 1
        text_cursor.setPosition(self.edit_pos, QtGui.QTextCursor.MoveMode.MoveAnchor)
        self.ui.textEdit.setTextCursor(text_cursor)

    def edit_mode_off(self):
        """ Show widgets.
        Try and set cursor position to 'current text' position.
        but this may have changed. """

        self.ui.groupBox.show()
        self.ui.groupBox_edit_mode.hide()
        self.ui.listWidget.setEnabled(True)
        self.ui.groupBox_file_buttons.setEnabled(True)
        self.ui.groupBox_file_buttons.setMaximumSize(4000, 30)
        self.ui.groupBox_coding_buttons.setEnabled(True)
        self.ui.treeWidget.setEnabled(True)
        self.ui.widget_left.show()
        self.prev_text = ""
        if self.edit_mode_has_changed:
            self.text = self.ui.textEdit.toPlainText()
            self.file_['fulltext'] = self.text
            self.file_['end'] = len(self.text)
            cur = self.app.conn.cursor()
            cur.execute("update source set fulltext=? where id=?", (self.text, self.file_['id']))
            self.app.conn.commit()
            for item in self.code_deletions:
                cur.execute(item)
            self.app.conn.commit()
            self.code_deletions = []
            self.ed_update_codings()
            self.ed_update_annotations()
            self.ed_update_casetext()
            # Update vectorstore
            if self.app.settings['ai_enable'] == 'True':
                self.app.ai.sources_vectorstore.import_document(self.file_['id'], self.file_['name'], self.text,
                                                                update=True)
            else:
                # AI is disabled. Delete the file from the vectorstore. It will be reimported later when the AI is enabled again. 
                self.app.ai.sources_vectorstore.delete_document(self.file_['id'])

        self.ui.textEdit.setTextInteractionFlags(  # make the textEdit read only by removing the 'TextEditable' flag
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        ) 
        self.ui.textEdit.installEventFilter(self.eventFilterTT)
        self.annotations = self.app.get_annotations()
        self.load_file(self.file_)
        self.update_file_tooltip()
        self.highlight()
        text_cursor = self.ui.textEdit.textCursor()
        if self.edit_pos > len(self.ui.textEdit.toPlainText()):
            self.edit_pos = len(self.ui.textEdit.toPlainText()) - 1
        text_cursor.setPosition(self.edit_pos, QtGui.QTextCursor.MoveMode.MoveAnchor)
        self.ui.textEdit.setTextCursor(text_cursor)

    def update_positions(self):
        """ Update positions for code text, annotations and case text as each character changes
        via adding or deleting. uses diff-match-patch module much faster than difflib, with large text files
        that are annotated, coded, cased.

        diff_match_patch.diff_main() Output:
        Adding X at pos 0
            [(1, 'X'), (0, "I rea...")]
        Adding X at pos 4
            [(0, 'I re'), (1, 'X'), (0, "ally...")]
        Adding X at end of file
            [(0, "...appy to pay €200."), (1, 'X')]
        Removing 'really'
            [(0, 'I '), (-1, 'really'), (0, " like ...")]

        """

        self.edit_mode_has_changed = True
        if self.no_codes_annotes_cases:
            return

        self.text = self.ui.textEdit.toPlainText()
        diff = diff_match_patch.diff_match_patch()
        diff_list = diff.diff_main(self.prev_text, self.text)
        # print(diff_list)
        extending = True
        preceding_pos = 0
        chars_len = 0
        pre_chars_len = 0
        post_chars_len = 0
        if len(diff_list) == 2 and diff_list[0][0] == 1:
            # print("Add at start")
            chars_len = len(diff_list[0][1])
            pre_chars_len = 0
            preceding_pos = 0
        if len(diff_list) == 2 and diff_list[0][0] == -1:
            # print("Remove from start")
            extending = False
            chars_len = len(diff_list[0][1])
            pre_chars_len = 0
            preceding_pos = 0
            post_chars_len = len(diff_list[1][1])
        if len(diff_list) == 2 and diff_list[1][0] == 1:
            # print("Add at end")
            chars_len = len(diff_list[1][1])
            pre_chars_len = len(diff_list[0][1])
            preceding_pos = pre_chars_len - 1
        if len(diff_list) == 2 and diff_list[1][0] == -1:
            # print("Remove from end")
            extending = False
            chars_len = len(diff_list[1][1])
            post_chars_len = 0
            pre_chars_len = len(diff_list[0][1])
            preceding_pos = pre_chars_len - 1
        if len(diff_list) == 3 and diff_list[1][0] == 1:
            # print("Add in middle")
            chars_len = len(diff_list[1][1])
            pre_chars_len = len(diff_list[0][1])
            preceding_pos = pre_chars_len - 1
        if len(diff_list) == 3 and diff_list[1][0] == -1:
            # print("Delete from middle")
            extending = False
            chars_len = len(diff_list[1][1])
            pre_chars_len = len(diff_list[0][1])
            preceding_pos = pre_chars_len - 1
            post_chars_len = len(diff_list[2][1])
        # Adding characters
        if extending:
            for c in self.ed_codetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] += chars_len
                    c['newpos1'] += chars_len
                    # Also check and apply start of code is at start of text
                    if c['pos0'] == 0:
                        c['newpos0'] = 0
                    changed = True
                if not changed and c['newpos0'] is not None and c['newpos0'] < preceding_pos < c['newpos1']:
                    c['newpos1'] += chars_len

            for c in self.ed_annotations:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] += chars_len
                    c['newpos1'] += chars_len
                    changed = True
                if c['newpos0'] is not None and not changed and c['newpos0'] < preceding_pos < c['newpos1']:
                    c['newpos1'] += chars_len

            for c in self.ed_casetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] += chars_len
                    # check and apply start of case is included
                    if c['pos0'] == 0:
                        c['newpos0'] = 0
                    c['newpos1'] += chars_len
                    changed = True
                if c['newpos0'] is not None and not changed and c['newpos0'] < preceding_pos < c['newpos1']:
                    c['newpos1'] += chars_len
            self.ed_highlight()
            self.prev_text = copy(self.text)
            return
        # Removing characters
        if not extending:
            for c in self.ed_codetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] -= chars_len
                    if c['newpos0'] < 0:
                        c['newpos0'] = 0
                    c['newpos1'] -= chars_len
                    changed = True
                # Remove, as entire text is being removed (e.g. copy replace)
                if c['newpos0'] is not None and not changed and c['newpos0'] >= preceding_pos and \
                        c['newpos1'] < preceding_pos - pre_chars_len + post_chars_len:
                    c['newpos0'] -= chars_len
                    if c['newpos0'] < 0:
                        c['newpos0'] = 0
                    c['newpos1'] -= chars_len
                    changed = True
                    self.code_deletions.append(f"delete from code_text where ctid={c['ctid']}")
                    c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newpos0'] < preceding_pos <= c['newpos1']:
                    c['newpos1'] -= chars_len
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append(f"delete from code_text where ctid={c['ctid']}")
                        c['newpos0'] = None

            for c in self.ed_annotations:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] -= chars_len
                    if c['newpos0'] < 0:
                        c['newpos0'] = 0
                    c['newpos1'] -= chars_len
                    changed = True
                    # Remove, as entire text is being removed (e.g. copy replace)
                    if not changed and c['newpos0'] >= preceding_pos and \
                            c['newpos1'] < preceding_pos - pre_chars_len + post_chars_len:
                        c['newpos0'] -= chars_len
                        c['newpos1'] -= chars_len
                        changed = True
                        self.code_deletions.append(f"delete from annotations where anid={c['anid']}")
                        c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newpos0'] < preceding_pos <= c['newpos1']:
                    c['newpos1'] -= chars_len
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append(f"delete from annotation where anid={c['anid']}")
                        c['newpos0'] = None

            for c in self.ed_casetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= preceding_pos and \
                        c['newpos0'] >= preceding_pos - pre_chars_len:
                    c['newpos0'] -= chars_len
                    if c['newpos0'] < 0:
                        c['newpos0'] = 0
                    c['newpos1'] -= chars_len
                    changed = True
                # Remove, as entire text is being removed (e.g. copy replace)
                if c['newpos0'] is not None and not changed and c['newpos0'] >= preceding_pos and \
                        c['newpos1'] < preceding_pos - pre_chars_len + post_chars_len:
                    c['newpos0'] -= chars_len
                    if c['newpos0'] < 0:
                        c['newpos0'] = 0
                    c['newpos1'] -= chars_len
                    changed = True
                    self.code_deletions.append(f"delete from case_text where id={c['id']}")
                    c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newpos0'] < preceding_pos <= c['newpos1']:
                    c['newpos1'] -= chars_len
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append(f"delete from case_text where id={c['id']}")
                        c['newpos0'] = None
        self.ed_highlight()
        self.prev_text = copy(self.text)

    '''def update_positions_difflib(self):
        """ OLD keep in case needed in future. 
        Update positions for code text, annotations and case text as each character changes
        via adding or deleting.

        Output: adding an e at pos 4:
        ---

        +++

        @@ -4,0 +5 @@

        +e
        """
        
        self.edit_mode_has_changed = True

        # No need to update positions (unless entire file is a case)
        if self.no_codes_annotes_cases or not self.edit_mode:
            return
        self.text = self.ui.textEdit.toPlainText()
        # n is how many context lines to show
        d = list(difflib.unified_diff(self.prev_text, self.text, n=0))
        if len(d) < 4:
            return
        char = d[3]
        position = d[2][4:]  # Removes prefix @@ -
        position = position[:-4]  # Removes suffix space@@\n
        previous = position.split(" ")[0]
        pre_start = int(previous.split(",")[0])
        pre_chars = None
        try:
            pre_chars = previous.split(",")[1]
        except IndexError:
            pass
        post = position.split(" ")[1]
        post_start = int(post.split(",")[0])
        post_chars = None
        try:
            post_chars = post.split(",")[1]
        except IndexError:
            pass
        # No additions or deletions
        if pre_start == post_start and pre_chars == post_chars:
            self.highlight()
            self.prev_text = copy(self.text)
            return

        """
        Adding 'X' at inserted position 5, note: None as no number is provided from difflib
        +X  previous 4 0  post 5 None

        Adding 'qda' at inserted position 5 (After 'This')
        +q  previous 4 0  post 5 3

        Removing 'X' from position 5, note None
        -X  previous 5 None  post 4 0

        Removing 'the' from position 13
        -t  previous 13 3  post 12 0
        """
        if pre_chars is None:
            pre_chars = 1
        pre_chars = -1 * int(pre_chars)  # String if not None
        if post_chars is None:
            post_chars = 1
        post_chars = int(post_chars)  # String if not None
        # print("XXX", char, " previous", pre_start, pre_chars, " post", post_start, post_chars)
        # Adding characters
        if char[0] == "+":
            for c in self.ed_codetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                if not changed and c['newpos0'] < pre_start < c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
            for c in self.ed_annotations:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                if c['newpos0'] is not None and not changed and c['newpos0'] < pre_start < c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
            for c in self.ed_casetext:
                changed = False
                # print("newpos0", c['newpos0'], "pre start", pre_start)
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                if c['newpos0'] is not None and not changed and c['newpos0'] < pre_start < c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
            self.ed_highlight()
            self.prev_text = copy(self.text)
            return

        # Removing characters
        if char[0] == "-":
            for c in self.ed_codetext:
                changed = False
                # print("CODE newpos0", c['newpos0'], "pre start", pre_start, pre_chars, post_chars)
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                # Remove, as entire text is being removed (e.g. copy replace)
                # print(changed, c['newpos0'],  pre_start, c['newpos1'], pre_chars, post_chars)
                # print(c['newpos0'], ">",  pre_start, "and", c['newpos1'], "<", pre_start + -1*pre_chars + post_chars)
                if c['newpos0'] is not None and not changed and c['newpos0'] >= pre_start and \
                        c['newpos1'] < pre_start + -1 * pre_chars + post_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                    self.code_deletions.append("delete from code_text where ctid=" + str(c['ctid']))
                    c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newpos0'] < pre_start <= c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append("delete from code_text where ctid=" + str(c['ctid']))
                        c['newpos0'] = None
            for c in self.ed_annotations:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                    # Remove, as entire text is being removed (e.g. copy replace)
                    # print(changed, c['newpos0'],  pre_start, c['newpos1'], pre_chars, post_chars)
                    # print(c['newpos0'], ">",  pre_start, "and", c['newpos1'], "<", pre_start + -1*pre_chars + post_chars)
                    if not changed and c['newpos0'] >= pre_start and c['newpos1'] < pre_start + -1 * pre_chars + post_chars:
                        c['newpos0'] += pre_chars + post_chars
                        c['newpos1'] += pre_chars + post_chars
                        changed = True
                        self.code_deletions.append("delete from annotations where anid=" + str(c['anid']))
                        c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newpos0'] < pre_start <= c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append("delete from annotation where anid=" + str(c['anid']))
                        c['newpos0'] = None
            for c in self.ed_casetext:
                changed = False
                if c['newpos0'] is not None and c['newpos0'] >= pre_start and c['newpos0'] >= pre_start + -1 * pre_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                # Remove, as entire text is being removed (e.g. copy replace)
                # print(changed, c['newpos0'],  pre_start, c['nepos1'], pre_chars, post_chars)
                # print(c['newpos0'], ">",  pre_start, "and", c['nepos1'], "<", pre_start + -1*pre_chars + post_chars)
                if c['newpos0'] is not None and not changed and c['newpos0'] >= pre_start and \
                        c['newpos1'] < pre_start + -1 * pre_chars + post_chars:
                    c['newpos0'] += pre_chars + post_chars
                    c['newpos1'] += pre_chars + post_chars
                    changed = True
                    self.code_deletions.append("delete from case_text where id=" + str(c['id']))
                    c['newpos0'] = None
                if c['newpos0'] is not None and not changed and c['newos0'] < pre_start <= c['newpos1']:
                    c['newpos1'] += pre_chars + post_chars
                    if c['newpos1'] < c['newpos0']:
                        self.code_deletions.append("delete from case_text where id=" + str(c['id']))
                        c['newpos0'] = None
        self.ed_highlight()
        self.prev_text = copy(self.text)'''

    def ed_highlight(self):
        """ Add coding and annotation highlights. """

        if not self.edit_mode:
            return
        self.remove_formatting()
        format_ = QtGui.QTextCharFormat()
        format_.setFontFamily(self.app.settings['font'])
        format_.setFontPointSize(self.app.settings['docfontsize'])
        self.ui.textEdit.blockSignals(True)
        cursor = self.ui.textEdit.textCursor()
        for item in self.ed_casetext:
            if item['newpos0'] is not None:
                cursor.setPosition(int(item['newpos0']), QtGui.QTextCursor.MoveMode.MoveAnchor)
                cursor.setPosition(int(item['newpos1']), QtGui.QTextCursor.MoveMode.KeepAnchor)
                format_.setFontUnderline(True)
                format_.setUnderlineColor(QtCore.Qt.GlobalColor.green)
                cursor.setCharFormat(format_)
        for item in self.ed_annotations:
            if item['newpos0'] is not None:
                cursor.setPosition(int(item['newpos0']), QtGui.QTextCursor.MoveMode.MoveAnchor)
                cursor.setPosition(int(item['newpos1']), QtGui.QTextCursor.MoveMode.KeepAnchor)
                format_.setFontUnderline(True)
                format_.setUnderlineColor(QtCore.Qt.GlobalColor.yellow)
                cursor.setCharFormat(format_)
        for item in self.ed_codetext:
            if item['newpos0'] is not None:
                cursor.setPosition(int(item['newpos0']), QtGui.QTextCursor.MoveMode.MoveAnchor)
                cursor.setPosition(int(item['newpos1']), QtGui.QTextCursor.MoveMode.KeepAnchor)
                format_.setFontUnderline(True)
                format_.setUnderlineColor(QtCore.Qt.GlobalColor.red)
                cursor.setCharFormat(format_)
        self.ui.textEdit.blockSignals(False)

    def remove_formatting(self):
        """ Remove formatting from text edit on changed text.
         Useful when pasting mime data (rich text or html) from clipboard. """

        self.ui.textEdit.blockSignals(True)
        format_ = QtGui.QTextCharFormat()
        format_.setFontFamily(self.app.settings['font'])
        format_.setFontPointSize(self.app.settings['docfontsize'])
        cursor = self.ui.textEdit.textCursor()
        cursor.setPosition(0, QtGui.QTextCursor.MoveMode.MoveAnchor)
        cursor.setPosition(len(self.ui.textEdit.toPlainText()), QtGui.QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(format_)
        self.ui.textEdit.blockSignals(False)

    def get_cases_codings_annotations(self):
        """ Get all linked cases, coded text and annotations for this file.
         For editing mode. """

        cur = self.app.conn.cursor()
        sql = "select ctid, cid, pos0, pos1, seltext, owner from code_text where fid=?"
        cur.execute(sql, [self.file_['id']])
        res = cur.fetchall()
        self.ed_codetext = []
        for r in res:
            self.ed_codetext.append({'ctid': r[0], 'cid': r[1], 'pos0': r[2], 'pos1': r[3], 'seltext': r[4],
                                     'owner': r[5], 'newpos0': r[2], 'newpos1': r[3]})
        sql = "select anid, pos0, pos1 from annotation where fid=?"
        cur.execute(sql, [self.file_['id']])
        res = cur.fetchall()
        self.ed_annotations = []
        for r in res:
            self.ed_annotations.append({'anid': r[0], 'pos0': r[1], 'pos1': r[2],
                                        'newpos0': r[1], 'newpos1': r[2]})
        sql = "select id, pos0, pos1 from case_text where fid=?"
        cur.execute(sql, [self.file_['id']])
        res = cur.fetchall()
        self.ed_casetext = []
        for r in res:
            self.ed_casetext.append({'id': r[0], 'pos0': r[1], 'pos1': r[2],
                                     'newpos0': r[1], 'newpos1': r[2]})
        self.no_codes_annotes_cases = False
        if self.ed_casetext == [] and self.ed_annotations == [] and self.ed_codetext == []:
            self.no_codes_annotes_cases = True

    def ed_update_casetext(self):
        """ Update linked case text positions. """

        sql = "update case_text set pos0=?, pos1=? where id=? and (pos0 !=? or pos1 !=?)"
        cur = self.app.conn.cursor()
        for c in self.ed_casetext:
            if c['newpos1'] >= len(self.text):
                c['newpos1'] = len(self.text)
            if c['newpos0'] is not None:
                cur.execute(sql, [c['newpos0'], c['newpos1'], c['id'], c['newpos0'], c['newpos1']])
            else:
                cur.execute("delete from case_text where id=?", [c['id']])
        self.app.conn.commit()

    def ed_update_annotations(self):
        """ Update annotation positions. """

        sql = "update annotation set pos0=?, pos1=? where anid=? and (pos0 !=? or pos1 !=?)"
        cur = self.app.conn.cursor()
        for a in self.ed_annotations:
            if a['newpos0'] is not None:
                cur.execute(sql, [a['newpos0'], a['newpos1'], a['anid'], a['newpos0'], a['newpos1']])
            if a['newpos1'] is None:
                cur.execute("delete from annotation where anid=?", [a['anid']])
        self.app.conn.commit()

    def ed_update_codings(self):
        """ Update coding positions and seltext. """

        cur = self.app.conn.cursor()
        sql = "update code_text set pos0=?, pos1=?, seltext=? where ctid=?"
        for c in self.ed_codetext:
            if c['newpos0'] is not None:
                seltext = self.text[c['newpos0']:c['newpos1']]
                cur.execute(sql, [c['newpos0'], c['newpos1'], seltext, c['ctid']])
            if c['newpos1'] >= len(self.text):
                cur.execute("delete from code_text where ctid=?", [c['ctid']])
        self.app.conn.commit()

    # AI functions

    def tab_changed(self):
        """Will be called when the user changes between the tabs "documents" and
        "AI assistance"
        """
        self.fill_code_counts_in_tree()

    def ai_search_clicked(self):
        """ Start the AI search (if the AI is ready and edit_mode is not active).   
        
        This will open a DialogAISearch to collect the search parameters and then 
        start phase 1 of the search, looking for suitable chunks of data in the vectorstore.
        """
        if self.edit_mode:
            msg = _('Please finish editing the text before starting an AI search.')
            Message(self.app, _('AI Search'), msg, "warning").exec()
            return
        if self.app.ai.get_status() == 'disabled':
            msg = _('The AI is disabled. Go to "AI > Setup Wizard" first.')
            Message(self.app, _('AI Search'), msg, "warning").exec()
            return
        if self.ai_search_running:
            msg = _('The AI is already performing a search. Please stop it before starting a new one.')
            Message(self.app, _('AI Search'), msg, "warning").exec()
            return
        if not self.app.ai.is_ready():
            msg = _('The AI is busy, please wait a moment and retry.')
            Message(self.app, _('AI Search'), msg, "warning").exec()
            return

        # Get currently selected item in code tree
        code_item = self.ui.treeWidget.currentItem()
        if code_item is None:  # nothing selected
            selected_id = -1
            selected_is_code = False
        elif code_item.text(1)[0:3] == 'cat':  # category selected
            selected_id = int(code_item.text(1).split(':')[1])
            selected_is_code = False
        else:  # code selected
            selected_id = int(code_item.text(1).split(':')[1])
            selected_is_code = True

        ui = DialogAiSearch(self.app, 'search', selected_id, selected_is_code)
        ret = ui.exec()
        if ret == QtWidgets.QDialog.DialogCode.Accepted:
            self.ai_search_code_name = ui.selected_code_name
            self.ai_search_code_memo = ui.selected_code_memo
            self.ai_include_coded_segments = ui.include_coded_segments
            self.ai_search_file_ids = ui.selected_file_ids
            self.ai_search_code_ids = ui.selected_code_ids
            self.ai_search_similar_chunk_list = []
            self.ai_search_chunks_pos = 0
            self.ai_search_results = []
            self.ai_search_prompt = ui.current_prompt
            self.ai_search_ai_model = self.app.ai_models[int(self.app.settings['ai_model_index'])]['name']

            # Prepare the UI
            self.ai_search_running = True
            self.ui.pushButton_ai_search.setText(self.ai_search_code_name)
            self.ui.pushButton_ai_search.setStyleSheet('text-align: left')
            self.ui.listWidget_ai.clear()
            self.ai_search_current_result_index = None
            self.ai_search_spinner_timer.start(500)
            self.ui.textEdit.setText(_('Searching for related data, please wait...'))

            # Phase 1: find similar chunks of data from the vectorstore
            self.app.ai.ai_async_is_canceled = False
            self.ai_search_chunks_pos = 0
            self.app.ai.retrieve_similar_data(self.ai_search_prepare_analysis,
                                              self.ai_search_code_name, self.ai_search_code_memo,
                                              self.ai_search_file_ids)

    def ai_search_prepare_analysis(self, chunks):
        """ Prepare and start the second step of the AI search. 
        
        This will clean up the list of data found in the first stage of the search and then 
        start step 2, the deeper analysis with the choosen search prompt.
        """

        if self.app.ai.ai_async_is_canceled:
            self.ai_search_running = False
            self.ui.textEdit.setText('')
            return
        if chunks is None or len(chunks) == 0:
            self.ui.textEdit.setText('')
            msg = _('AI: No related data found for "') + self.ai_search_code_name + '".'
            Message(self.app, _('AI Search'), msg, "warning").exec()
            self.ai_search_running = False
            return

        # 1) Check if we search for data related to a code (instead of freetext) and filter out 
        # chunks that are already coded with this code. This way, we find new data only.  
        if (not self.ai_include_coded_segments) and self.ai_search_code_ids is not None and len(
                self.ai_search_code_ids) > 0:
            filtered_chunks = []
            for chunk in chunks:
                chunk_already_coded = False
                chunk_source_id = chunk.metadata['id']
                chunk_start = chunk.metadata['start_index']
                chunk_end = chunk_start + len(chunk.page_content)
                code_ids_str = "(" + ", ".join(map(str, self.ai_search_code_ids)) + ")"
                codings_sql = f'select pos0, pos1 from code_text where fid={chunk_source_id} AND cid in {code_ids_str}'
                cur = self.app.conn.cursor()
                cur.execute(codings_sql)
                codings = cur.fetchall()
                for row in codings:
                    # Calculate the overlap by finding the maximum start position and the minimum end position
                    coding_start = int(row[0])
                    coding_end = int(row[1])
                    overlap_start = max(chunk_start, coding_start)
                    overlap_end = min(chunk_end, coding_end)
                    if overlap_start < overlap_end:
                        # found an overlap. If it is more then 10% of the coding, skip this chunk
                        overlap_len = overlap_end - overlap_start
                        coding_len = coding_end - coding_start
                        if overlap_len > 0.1 * coding_len:
                            chunk_already_coded = True
                            break
                if not chunk_already_coded:
                    filtered_chunks.append(chunk)
            # finally: replace the chunks list with the filtered one
            chunks = filtered_chunks

        if len(chunks) == 0:
            self.ui.textEdit.setText('')
            msg = _('AI: No new data found for "') + self.ai_search_code_name + _(
                '" beside what has already been coded with this code.')
            Message(self.app, _('AI Search'), msg, "warning").exec()
            self.ai_search_running = False
            return

        self.ui.textEdit.setText(_('Potentially related data found, inspecting it closer. Please be patient...'))

        # 2) Send the first "ai_search_analysis_max_count" chunks to the llm for further analysis 
        self.ai_search_similar_chunk_list = chunks  # save to allow analyzing more chunks later
        self.ai_search_chunks_pos = 0  # position of the next chunk to be analyzed
        self.ai_search_analysis_counter = 0  # counter to stop analyzing after ai_search_analysis_max_count
        self.ai_search_found = False  # Becomes True if any new data has been found
        self.ai_search_analyze_next_chunk()

    def ai_search_analyze_next_chunk(self):
        """Step 2 of the AI search: 
        Selects the next chunk of data found in step 1 of the search and analyzes it closer, 
        using the selected search prompt.
        This will be repeated until: 
        1) 'ai_search_analysis_max_count' is reached (in which case the analysis is paused and
        the user has to click on 'find more' to continue), or 
        2) 'len(self.ai_search_similar_chunk_list)' is reached, meaning that all the 
        chunks found in step 1 have been analyzed and the search is finished."""

        if self.ai_search_chunks_pos < len(self.ai_search_similar_chunk_list):
            # still chunks left for analysis            
            if self.ai_search_analysis_counter < ai_search_analysis_max_count:
                # ai_search_analysis_max_count not reached
                self.ai_search_running = True
                self.app.ai.search_analyze_chunk(self.ai_search_analyze_next_chunk_callback,
                                                 self.ai_search_similar_chunk_list[self.ai_search_chunks_pos],
                                                 self.ai_search_code_name,
                                                 self.ai_search_code_memo,
                                                 self.ai_search_prompt)
            else:  # ai_search_analysis_max_count reached
                self.ai_search_running = False
                if len(self.ai_search_results) == 0:  # nothing found
                    self.ai_search_update_listview_action()
                    self.ui.textEdit.setText('')
                    msg = _('The closer inspection of the first ') + str(self.ai_search_chunks_pos) + \
                          _('pieces of data yielded no results. You can continue to inspect more by clicking on "find '
                            'more" in the list on the left.')
                    Message(self.app, _('AI Search'), msg, "warning").exec()
        else:  # search finished
            self.ai_search_running = False
            if len(self.ai_search_results) == 0:  # nothing found
                self.ui.textEdit.setText('')
                self.ai_search_update_listview_action()
                msg = _(
                    'Upon closer inspection, no pieces of data relevant to your search query could be identified. '
                    'Please start a new search.')
                Message(self.app, _('AI Search'), msg, "warning").exec()

        self.ai_search_update_listview_action()

    def ai_search_analyze_next_chunk_callback(self, doc):
        """Callback for ai_search_analyze_next_chunk: 
        If the AI has finished analyzing the chunk of data, this callback function collects the results, 
        updates the UI and starts the analysis of the next chunk. 
        """

        if not self.ai_search_running:  # Search has been cancelled
            return
        if doc is not None:
            self.ai_search_results.append(doc)
            item_text = f'{doc["metadata"]["name"]}: '
            item_text += '"' + str(doc['quote']).replace('\n', ' ') + '"'
            item = QtWidgets.QListWidgetItem(item_text)
            item_tooltip = '<p>' + _('Quote: ') + f'<i>"{doc["quote"]}"</i></p>'
            item_tooltip += '<p>' + _('AI interpretation: ') + doc["interpretation"] + '</p>'
            item.setToolTip(item_tooltip)
            self.ui.listWidget_ai.insertItem(len(self.ai_search_results) - 1, item)
            if not self.ai_search_found:  # first item found
                self.ai_search_found = True
                item.setSelected(True)
                self.ai_search_selection_changed()

        # analyze next
        self.ai_search_chunks_pos += 1
        self.ai_search_analysis_counter += 1
        if not self.app.ai.ai_async_is_canceled:
            self.ai_search_analyze_next_chunk()
        else:
            self.ai_search_running = False

    def ai_search_update_listview_action(self):
        """Adding a special item to the end of the list view that can be clicked for certain actions:
        - Find more: Shown if there are still chunks of empirical data left from stage 1 to be analyzed in stage 2 
        - Stop search: Shown if a search is actually running in the background
        - (search finished): Shown if all results from stage 1 have already been analyzed  
        """
        # add action item to the list if necessary
        if self.ui.listWidget_ai.count() <= len(self.ai_search_results):
            self.ui.listWidget_ai.addItem('')
            self.ai_search_listview_action_label = None
        action_item = self.ui.listWidget_ai.item(self.ui.listWidget_ai.count() - 1)
        if self.ai_search_listview_action_label is None:
            self.ai_search_listview_action_label = QtWidgets.QLabel('')
            self.ai_search_listview_action_label.setStyleSheet(
                f'QLabel {{color: {self.app.highlight_color()}; text-decoration: underline; margin-left: 2px; }}')
            self.ui.listWidget_ai.setItemWidget(action_item, self.ai_search_listview_action_label)

        if self.ai_search_running:
            # Stop search
            action_item.setText('')
            self.ai_search_listview_action_label.setText(_('>> Searching (click here to cancel)') +
                                                         self.ai_search_spinner_sequence[self.ai_search_spinner_index])
            self.ai_search_listview_action_label.setToolTip(_('Click here to stop the search'))
            self.ai_search_listview_action_label.setVisible(True)
        elif self.ai_search_chunks_pos < len(self.ai_search_similar_chunk_list):
            # Find more
            action_item.setText('')
            self.ai_search_listview_action_label.setText(_('>> Find more...'))
            self.ai_search_listview_action_label.setToolTip(_('Click here to analyze more data'))
            self.ai_search_listview_action_label.setVisible(True)
        else:
            # Search finished
            self.ai_search_listview_action_label.setText('')
            self.ai_search_listview_action_label.setToolTip('')
            self.ai_search_listview_action_label.setVisible(False)
            if self.app.ai.ai_async_is_errored:
                action_item.setText('(search aborted due to an error)')
            else:
                action_item.setText('(search finished)')

    def ai_search_list_clicked(self):
        """ Checks if the special action item at the end of the list was clicked 
        and performs the corresponding action ('find more', 'stop search', etc.).
        If a normal item in the list was clicked, 'self.ai_search_selection_changed()' is
        called."""

        row = self.ui.listWidget_ai.currentRow()
        if row < len(self.ai_search_results):  # clicked on a search result
            self.ai_search_selection_changed()
        else:  # clicked on "stop search" or "find more"
            selection_model = self.ui.listWidget_ai.selectionModel()
            selection_model.blockSignals(True)  # stop selection_change from beeing issued
            if self.ai_search_running:  # stop search
                msg = _('Do you want to stop the search?')
                msg_box = Message(self.app, _("Open file"), msg, "information")
                msg_box.setStandardButtons(
                    QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Abort)
                msg_box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Ok)
                ret = msg_box.exec()
                if ret == QtWidgets.QMessageBox.StandardButton.Ok:
                    self.app.ai.ai_async_is_canceled = True
                    self.ai_search_running = False
                    self.ai_search_update_listview_action()
            else:  # 'find more' item or "finished search"
                if self.ai_search_chunks_pos >= len(self.ai_search_similar_chunk_list):
                    msg = _('There are no more pieces of data to analyze for this search. Please start a new search.')
                    Message(self.app, _('AI Search'), msg, "warning").exec()
                elif self.ai_search_running or (not self.app.ai.is_ready()):
                    msg = _('The AI is busy. Please wait a moment and retry.')
                    Message(self.app, _('AI Search'), msg, "warning").exec()
                else:
                    self.ai_search_analysis_counter = 0  # counter to stop analyzing after ai_search_analysis_max_count
                    self.ai_search_running = True
                    self.ai_search_spinner_timer.start()
                    self.ai_search_analyze_next_chunk()
            # reselect the item that was active before:
            if self.ai_search_current_result_index is not None:
                self.ui.listWidget_ai.setCurrentRow(self.ai_search_current_result_index)
            else:
                self.ui.listWidget_ai.clearSelection()
            selection_model.blockSignals(False)

    def ai_search_selection_changed(self):
        """Load the document corresponding to the selected AI search result in the textView 
        and select the quote that the AI chose."""

        if self.ai_search_results is None or len(self.ai_search_results) == 0:
            return

        if len(self.ui.listWidget_ai.selectedIndexes()) > 0:
            row = self.ui.listWidget_ai.selectedIndexes()[0].row()
        else:
            self.ai_search_current_result_index = None
            return

        if row == len(self.ai_search_results):
            # out of bounds, must be the action item
            return

        self.ai_search_current_result_index = row
        doc = self.ai_search_results[self.ai_search_current_result_index]
        id_ = doc['metadata']['id']
        quote_start = doc['quote_start']
        quote_end = quote_start + len(doc['quote'])
        self.open_doc_selection(id_, quote_start, quote_end)

    def open_doc_selection(self, doc_id, sel_start, sel_end):
        """ Open document and select a certain part. """

        for i, f in enumerate(self.filenames):
            if f['id'] == doc_id:
                f['start'] = 0
                if f['end'] != f['characters']:  # partially loaded
                    msg = _("Entire text file will be loaded")
                    Message(self.app, _('Information'), msg).exec()
                f['end'] = f['characters']
                try:
                    self.ui.listWidget.setCurrentRow(i)
                    self.load_file(f)
                    # Set text cursor position
                    text_cursor = self.ui.textEdit.textCursor()
                    text_cursor.setPosition(sel_start)
                    endpos = sel_end
                    if endpos < 0:
                        endpos = 0
                    text_cursor.setPosition(endpos, QtGui.QTextCursor.MoveMode.KeepAnchor)
                    self.ui.textEdit.setTextCursor(text_cursor)
                    self.ui.textEdit.setFocus()
                    QtCore.QTimer.singleShot(20, self.scroll_text_into_view)  # scroll into view after window is updated
                except Exception as e:
                    logger.debug(str(e))
                break

    def scroll_text_into_view(self):
        """ Scroll so the selection is in the middle of the screen. """
        cursor = self.ui.textEdit.textCursor()
        if cursor.hasSelection():
            cursor_rect = self.ui.textEdit.cursorRect(cursor)
            vertical_center = cursor_rect.center().y()
            vertical_scrollbar = self.ui.textEdit.verticalScrollBar()
            current_scroll_pos = vertical_scrollbar.value()
            desired_scroll_pos = current_scroll_pos + (vertical_center - self.ui.textEdit.viewport().height() // 2)
            vertical_scrollbar.setValue(desired_scroll_pos)

    def ai_search_update_spinner(self):
        """ Updating the ai_progressBar and the text spinner in the list view to indicate to the user that 
        an AI search is running in the background. """
        if self.app.ai.ai_async_is_finished or self.app.ai.ai_async_is_errored or self.app.ai.ai_async_is_canceled:
            self.ai_search_running = False
        if self.ai_search_running:
            self.ui.ai_progressBar.setVisible(True)
            self.ai_search_spinner_index = (self.ai_search_spinner_index + 1) % len(self.ai_search_spinner_sequence)
            self.ai_search_update_listview_action()
        else:
            self.ui.ai_progressBar.setVisible(False)
            self.ai_search_spinner_timer.stop()
            self.ai_search_update_listview_action()

    def get_overlapping_ai_search_result(self):
        """
        Retrieves the single document from self.ai_search_results that has the longest overlap 
        with the text selection defined in the text editor within the UI.
        This is used in 'self.mark()' to determine if the AI has analyzed a certain text passage and
        we can suggest adding the interpretation to the coding memo.

        Returns:
            best_doc (dict or None): The document with the longest overlap if overlapping documents 
                exist; otherwise, None.
        """

        if self.ui.tabWidget.currentIndex() != 1:  # not in ai search mode
            return

        # Get the adjusted start and end positions from the text editor's current selection
        pos0 = self.ui.textEdit.textCursor().selectionStart() + self.file_['start']
        pos1 = self.ui.textEdit.textCursor().selectionEnd() + self.file_['start']
        if pos0 == pos1:
            return

        best_doc = None
        max_overlap_length = 0

        for doc in self.ai_search_results:
            quote_start = doc['quote_start']
            quote_end = quote_start + len(doc['quote'])

            # Check for any intersection between the quote interval and the selection interval
            if quote_start <= pos1 and quote_end >= pos0:
                # Calculate the overlap length
                overlap_start = max(quote_start, pos0)
                overlap_end = min(quote_end, pos1)
                overlap_length = overlap_end - overlap_start

                # Update the best_doc if this document has a longer overlap
                if overlap_length > max_overlap_length:
                    max_overlap_length = overlap_length
                    best_doc = doc

        return best_doc


class ToolTipEventFilter(QtCore.QObject):
    """ Used to add a dynamic tooltip for the textEdit.
    The tool top text is changed according to its position in the text.
    If over a coded section the codename(s) or Annotation note are displayed in the tooltip.
    """

    codes = None
    code_text = None
    annotations = None
    file_id = None
    offset = 0
    app = None

    def set_codes_and_annotations(self, app, code_text, codes, annotations, file_):
        """ Code_text contains the coded text to be displayed in a tooptip.
        Annotations - a mention is made if current position is annotated

        param:
            code_text: List of dictionaries of the coded text contains: pos0, pos1, seltext, cid, memo
            codes: List of dictionaries contains id, name, color
            annotations: List of dictionaries of
            offset: integer 0 if all the text is loaded, other numbers mean a portion of the text is loaded,
            beginning at the offset
        """

        self.app = app
        self.code_text = code_text
        self.codes = codes
        self.annotations = annotations
        self.file_id = file_['id']
        self.offset = file_['start']
        for item in self.code_text:
            for c in self.codes:
                if item['cid'] == c['cid']:
                    item['name'] = c['name']
                    item['color'] = c['color']

    def eventFilter(self, receiver, event):
        # QtGui.QToolTip.showText(QtGui.QCursor.pos(), tip)
        if event.type() == QtCore.QEvent.Type.ToolTip:
            cursor = receiver.cursorForPosition(event.pos())
            pos = cursor.position()
            receiver.setToolTip("")
            text_ = ""
            multiple_msg = '<p style="color:#f89407">' + _("Press O to cycle overlapping codes") + "</p>"
            multiple = 0
            # Occasional None type error
            if self.code_text is None:
                # Call Base Class Method to Continue Normal Event Processing
                return super(ToolTipEventFilter, self).eventFilter(receiver, event)
            for item in self.code_text:
                if item['pos0'] - self.offset <= pos <= item['pos1'] - self.offset and \
                        item['seltext'] is not None:
                    seltext = item['seltext']
                    seltext = seltext.replace("\n", "")
                    seltext = seltext.replace("\r", "")
                    # Selected text with a readable cut off, not cut off halfway through a word.
                    if len(seltext) > 90:
                        pre = seltext[0:40].split(' ')
                        post = seltext[len(seltext) - 40:].split(' ')
                        try:
                            pre = pre[:-1]
                        except IndexError:
                            pass
                        try:
                            post = post[1:]
                        except IndexError:
                            pass
                        seltext = " ".join(pre) + " ... " + " ".join(post)
                    try:
                        color = TextColor(item['color']).recommendation
                        text_ += '<p style="background-color:' + item['color'] + "; color:" + color + '"><em>'
                        text_ += item['name'] + "</em>"
                        if self.app.settings['showids']:
                            text_ += " [ctid:" + str(item['ctid']) + "]"
                        text_ += "<br />" + seltext
                        if item['memo'] != "":
                            memo_text = item['memo']
                            if len(memo_text) > 150:
                                memo_text = memo_text[:150] + "..."
                            text_ += "<br /><em>" + _("MEMO: ") + memo_text + "</em>"
                        if item['important'] == 1:
                            text_ += "<br /><em>IMPORTANT</em>"
                        text_ += "</p>"
                        multiple += 1
                    except Exception as e:
                        msg = "Codes ToolTipEventFilter Exception\n" + str(e) + ". Possible key error: \n"
                        msg += str(item)
                        logger.error(msg)
            if multiple > 1:
                text_ = multiple_msg + text_
            # Check annotations
            for ann in self.annotations:
                if ann['pos0'] - self.offset <= pos <= ann['pos1'] - self.offset and self.file_id == ann['fid']:
                    text_ += "<p>" + _("ANNOTATED:") + ann['memo'] + "</p>"
            if text_ != "":
                receiver.setToolTip(text_)
        # Call Base Class Method to Continue Normal Event Processing
        return super(ToolTipEventFilter, self).eventFilter(receiver, event)


# see https://www.freeformatter.com/html-entities.html
entities = {"&": "&amp;", '"': '&quot;', "'": "&#39;", "<": "&lt;", ">": "&gt;", "–": "&ndash;", "—": "&mdash;",
            "€": "&euro;", "‘": "&lsquo;", "’": "&rsquo;", "“": "&ldquo;", "”": "&rdquo;", "…": "&hellip;",
            "™": "&trade;", "£": "&pound;"}
