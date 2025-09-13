# -*- coding: utf-8 -*-

"""
This file is part of QualCoder.

QualCoder is free software: you can redistribute it and/or modify it under the
terms of the GNU Lesser General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.

QualCoder is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with QualCoder.
If not, see <https://www.gnu.org/licenses/>.

Author: Colin Curtain (ccbogel)
https://github.com/ccbogel/QualCoder
"""

import logging
import os
import sqlite3

import qtawesome as qta  # see: https://pictogrammers.com/library/mdi/

from PyQt6 import QtGui, QtWidgets, QtCore

from .code_text import DialogCodeText  # for isinstance()
from .confirm_delete import DialogConfirmDelete
from .GUI.ui_special_functions import Ui_Dialog_special_functions
from .helpers import Message
from .merge_projects import MergeProjects
from .select_items import DialogSelectItems
from .text_file_replacement import ReplaceTextFile

path = os.path.abspath(os.path.dirname(__file__))
logger = logging.getLogger(__name__)


class DialogSpecialFunctions(QtWidgets.QDialog):
    """ Dialog for special QualCoder functions.
    """

    app = None
    parent_text_edit = None
    tab_coding = None  # Tab widget coding tab for updates

    # For Replacing a text file with another and keeping codings
    file_to_replace = None
    file_replacement = None

    # For merging projects
    merge_project_path = ""
    projects_merged = False  # Used in main to clear gui tabs

    def __init__(self, app, parent_text_edit, tab_coding, parent=None):

        super(DialogSpecialFunctions, self).__init__(parent)
        QtWidgets.QDialog.__init__(self)
        self.ui = Ui_Dialog_special_functions()
        self.ui.setupUi(self)
        self.app = app
        self.parent_text_edit = parent_text_edit
        self.tab_coding = tab_coding
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)
        font = f'font: {app.settings["fontsize"]}pt "{app.settings["font"]}";'
        self.setStyleSheet(font)
        self.merge_project_path = ""
        self.coder_names = []
        self.ui.pushButton_select_text_file.setIcon(qta.icon('mdi6.file-search', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_select_text_file.setFocus()
        self.ui.pushButton_select_replacement_text_file.setIcon(
            qta.icon('mdi6.file-search', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_select_project.setIcon(qta.icon('mdi6.file-search', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_text_starts.setIcon(qta.icon('mdi6.play', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_text_starts.clicked.connect(self.change_text_code_start_positions)
        self.ui.pushButton_text_ends.setIcon(qta.icon('mdi6.play', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_text_ends.clicked.connect(self.change_text_code_end_positions)
        self.ui.pushButton_text_update.setIcon(qta.icon('mdi6.play', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_select_text_file.pressed.connect(self.select_original_text_file)
        self.ui.pushButton_select_replacement_text_file.pressed.connect(self.select_replacement_text_file)
        self.ui.pushButton_text_update.setEnabled(False)
        self.ui.pushButton_text_update.pressed.connect(self.replace_file_update_codings)
        self.ui.pushButton_merge.setIcon(qta.icon('mdi6.play', options=[{'scale_factor': 1.4}]))
        self.ui.pushButton_select_project.pressed.connect(self.select_project_folder)
        self.ui.pushButton_merge.setEnabled(False)
        self.ui.pushButton_merge.pressed.connect(self.merge_projects)
        self.fill_combobox_codernames()
        self.ui.pushButton_rename.pressed.connect(self.rename_coder)
        # Text positions, is here in case it is needed, but hidden for users
        self.ui.groupBox_text_positions.hide()

    # Functions to merge external project into this project
    def select_project_folder(self):
        """ Select another .qda project """

        self.merge_project_path = ""
        default_directory = self.app.settings['directory']
        if default_directory == "":
            default_directory = os.path.expanduser('~')
        self.merge_project_path = QtWidgets.QFileDialog.getExistingDirectory(self,
                                                                             _('Open project directory'),
                                                                             default_directory)
        if self.merge_project_path is False or len(self.merge_project_path) < 5:
            Message(self.app, _("Error"), _("No project selected")).exec()
            return
        if self.merge_project_path[-4:] != ".qda":
            Message(self.app, _("Error"), _("Not a QualCoder project")).exec()
            return
        if self.merge_project_path == self.app.project_path:
            Message(self.app, _("Error"), _("The same project")).exec()
            return
        msg = _("Merge") + f"\n{self.merge_project_path}\n" + _("into") + f"\n{self.app.project_path}\n"
        msg += _("Press Run Button to merge projects")
        Message(self.app, _("Merge projects"), msg).exec()
        self.ui.pushButton_merge.setEnabled(True)
        self.ui.pushButton_merge.setFocus()

    def merge_projects(self):
        """ Merge selected project into this project. """

        mp = MergeProjects(self.app, self.merge_project_path)
        self.parent_text_edit.append(mp.summary_msg)
        self.projects_merged = mp.projects_merged

    # Functions for coder names deletion and editing
    def fill_combobox_codernames(self):
        """ Get coder names from all tables """

        sql = "select owner from code_image union select owner from code_text union select owner from code_av "
        sql += "union select owner from code_name union select owner from code_cat "
        sql += "union select owner from cases union select owner from case_text "
        sql += "union select owner from attribute union select owner from attribute_type "
        sql += "union select owner from source union select owner from annotation union select owner from journal "
        sql += "union select owner from manage_files_display union select owner from files_filter"
        self.coder_names = []
        if self.app.conn is not None:
            cur = self.app.conn.cursor()
            cur.execute(sql)
            results = cur.fetchall()
            for row in results:
                if row[0] != "":
                    self.coder_names.append(row[0])
        self.ui.comboBox_codername.clear()
        self.ui.comboBox_codername.addItems(self.coder_names)
        index = self.ui.comboBox_codername.findText(self.app.settings['codername'], QtCore.Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.ui.comboBox_codername.setCurrentIndex(index)

    def rename_coder(self):
        """ Rename a coder name. Rename can also be merged into an existing name. """

        selected_name = self.ui.comboBox_codername.currentText()
        new_name, ok = QtWidgets.QInputDialog.getText(None, _('New coder name'), _('New name:'))
        if not ok or new_name == "":
            Message(self.app, _("Rename coder"), _("No change"), "Information").exec()
            return
        msg = f"{selected_name} --> {new_name}\n" + _("Are you sure?")
        ui = DialogConfirmDelete(self.app, msg, _("Rename coder"))
        ok = ui.exec()
        if not ok:
            return
        sqls = ["update code_image set owner=? where owner=?",
                "update code_av set owner=? where owner=?",
                "update code_name set owner=? where owner=?",
                "update code_cat set owner=? where owner=?",
                "update cases set owner=? where owner=?",
                "update case_text set owner=? where owner=?",
                "update attribute set owner=? where owner=?",
                "update attribute_type set owner=? where owner=?",
                "update source set owner=? where owner=?",
                "update journal set owner=? where owner=?",
                "update manage_files_display set owner=? where owner=?",
                "update files_filter set owner=? where owner=?"]
        rename_success = True
        cur = self.app.conn.cursor()
        for sql in sqls:
            try:
                cur.execute(sql, [new_name, selected_name])
                self.app.conn.commit()
            except sqlite3.IntegrityError as e:
                self.parent_text_edit.append(f"<p>Renamed coder error: {selected_name} --> {new_name}</p><p>{e}</p>")
                rename_success = False

        # Code text has an extensive unique constraint across: cid, fid, pos0, pos1, owner
        cur.execute("select ctid from code_text where owner=?", [selected_name])
        ctid_res = cur.fetchall()
        for row in ctid_res:
            try:
                cur.execute("update code_text set owner=? where ctid=?", [new_name, row[0]])
                self.app.conn.commit()
            except sqlite3.IntegrityError:
                cur.execute("delete from code_text where ctid=?", [row[0]])
                self.app.conn.commit()
        # Annotation has an extensive unique constraint across: fid, pos0, pos1, owner
        cur.execute("select anid from annotation where owner=?", [selected_name])
        anid_res = cur.fetchall()
        for row in anid_res:
            try:
                cur.execute("update annotation set owner=? where anid=?", [new_name, row[0]])
                self.app.conn.commit()
            except sqlite3.IntegrityError:
                cur.execute("delete from annotation where anid=?", [row[0]])
                self.app.conn.commit()

        self.fill_combobox_codernames()
        if self.app.settings['codername'] == selected_name:
            self.app.settings['codername'] = new_name
            cur.execute("update project set codername=?", [new_name])
            self.app.conn.commit()
        # This is checked in main.special_functions and clears other tabs: coding etc
        self.projects_merged = True
        if rename_success:
            self.parent_text_edit.append(f"<p>Renamed coder: {selected_name} --> {new_name}</p>")
        else:
            self.parent_text_edit.append(f"<p>Renamed coder across most tables: {selected_name} --> {new_name}</p>")

    # Functions to update a text file but attempt to keep original codings
    def select_original_text_file(self):
        """ Select text file to replace. """

        self.file_to_replace = []
        file_texts = self.app.get_file_texts()
        ui = DialogSelectItems(self.app, file_texts, _("Delete files"), "single")
        ok = ui.exec()
        if not ok:
            return
        self.file_to_replace = ui.get_selected()
        if not self.file_to_replace:
            self.ui.pushButton_select_text_file.setToolTip(_("Select text file to replace"))
            return
        self.ui.pushButton_select_text_file.setToolTip(_("Replacing: ") + self.file_to_replace['name'])
        if self.file_to_replace and self.file_replacement:
            self.ui.pushButton_text_update.setEnabled(True)

    def select_replacement_text_file(self):
        """ Select replacement updated text file. """

        file_types = "Text Files (*.docx *.epub *.html *.htm *.md *.odt *.pdf *.txt)"
        filepath, ok = QtWidgets.QFileDialog.getOpenFileNames(None, _('Replacement file'),
                                                              self.app.settings['directory'], file_types)
        # options=QtWidgets.QFileDialog.Option.DontUseNativeDialog)
        if not ok or filepath == []:
            self.ui.pushButton_select_replacement_text_file.setToolTip(_("Select replacement text file"))
            return
        self.file_replacement = filepath[0]
        self.ui.pushButton_select_replacement_text_file.setToolTip(_("Replacement file: ") + self.file_replacement)
        if self.file_to_replace and self.file_replacement:
            self.ui.pushButton_text_update.setEnabled(True)
            self.ui.pushButton_text_update.setToolTip(_("Press to replace the text file"))

    def replace_file_update_codings(self):
        """ Requires two files - original and replacement to be selected before button is enabled.
        Called by:
         pushButton_text_update """

        if self.file_to_replace is None or self.file_replacement is None:
            Message(self.app, _("No files selected"), _("No existing or replacement file selected")).exec()
            return
        ReplaceTextFile(self.app, self.file_to_replace, self.file_replacement)
        self.file_to_replace = None
        self.ui.pushButton_select_text_file.setToolTip(_("Select text file to replace"))
        self.file_replacement = None
        self.ui.pushButton_select_replacement_text_file.setToolTip(_("Select replacement text file"))

    def change_text_code_start_positions(self):
        """ Extend or shrink text coding start positions in all codings and all files for owner. """

        delta = self.ui.spinBox_text_starts.value()
        if delta == 0:
            return
        cur = self.app.conn.cursor()
        sql = "select cid,fid,pos0,pos1,code_text.owner, length(source.fulltext) from code_text join source on source.id=code_text.fid where code_text.owner=?"
        text_sql = "select substr(source.fulltext, ?, ?) from source where source.id=?"
        update_sql = "update code_text set pos0=?, seltext=? where pos0=? and pos1=? and cid=? and fid=? and owner=?"
        cur.execute(sql, [self.app.settings['codername']])
        res = cur.fetchall()
        if not res:
            return
        msg = _("Change ALL text code start positions in ALL text files by ")
        msg += str(delta) + _(" characters.\n")
        msg += _("Made by coder: ") + self.app.settings['codername'] + "\n"
        msg += str(len(res)) + _(" to change.") + "\n"
        msg += _("Backup project before performing this function.\n")
        msg += _("Press OK to continue.")
        ui = DialogConfirmDelete(self.app, msg, _("Change code start positions"))
        ok = ui.exec()
        if not ok:
            return
        for r in res:
            new_pos0 = r[2] - delta
            # cannot have start pos less than start of text
            if new_pos0 < 0:
                new_pos0 = 0
            # cannot have start pos larger than end pos
            if new_pos0 > r[3]:
                new_pos0 = r[3] - 1
            cur.execute(text_sql, [new_pos0 + 1, r[3] - new_pos0, r[1]])
            seltext = ""
            try:
                seltext = cur.fetchone()[0]
            except TypeError:
                pass
            cur.execute(update_sql, [new_pos0, seltext, r[2], r[3], r[0], r[1], r[4]])
            self.app.conn.commit()
        self.parent_text_edit.append(
            _("All text codings by ") + self.app.settings['codername'] + _(" resized by ") + str(delta) + _(
                " characters."))
        self.update_tab_coding_dialog()

    def change_text_code_end_positions(self):
        """ Extend or shrink text coding start positions in all codings and all files for owner. """

        delta = self.ui.spinBox_text_ends.value()
        if delta == 0:
            return
        cur = self.app.conn.cursor()
        sql = "select cid,fid,pos0,pos1,code_text.owner, length(source.fulltext) from code_text join source on source.id=code_text.fid where code_text.owner=?"
        text_sql = "select substr(source.fulltext, ?, ?) from source where source.id=?"
        update_sql = "update code_text set pos1=?, seltext=? where pos0=? and pos1=? and cid=? and fid=? and owner=?"
        cur.execute(sql, [self.app.settings['codername']])
        res = cur.fetchall()
        if not res:
            return
        msg = _("Change ALL text code end positions in ALL text files by ")
        msg += str(delta) + _(" characters.\n")
        msg += _("Made by coder: ") + self.app.settings['codername'] + "\n"
        msg += str(len(res)) + _(" to change.") + "\n"
        msg += _("Backup project before performing this function.\n")
        msg += _("Press OK to continue.")
        ui = DialogConfirmDelete(self.app, msg, _("Change code end positions"))
        ok = ui.exec()
        if not ok:
            return
        for r in res:
            new_pos1 = r[3] + delta
            # cannot have end pos less or equal to startpos
            if new_pos1 <= r[2]:
                new_pos1 = r[2] + 1
            # cannot have end pos larger than text
            if new_pos1 >= r[5]:
                new_pos1 = r[5] - 1
            cur.execute(text_sql, [r[2] + 1, new_pos1 - r[2], r[1]])
            seltext = ""
            try:
                seltext = cur.fetchone()[0]
            except TypeError:
                pass
            cur.execute(update_sql, [new_pos1, seltext, r[2], r[3], r[0], r[1], r[4]])
            self.app.conn.commit()
        self.parent_text_edit.append(
            _("All text codings by ") + self.app.settings['codername'] + _(" resized by ") + str(delta) + _(
                " characters."))
        self.update_tab_coding_dialog()

    def update_tab_coding_dialog(self):
        """ DialogCodeText """

        contents = self.tab_coding.layout()
        if contents:
            # Remove code text widgets from layout
            for i in reversed(range(contents.count())):
                c = contents.itemAt(i).widget()
                if isinstance(c, DialogCodeText):
                    c.get_coded_text_update_eventfilter_tooltips()
                    break

    def accept(self):
        """ Overrride accept button. """

        super(DialogSpecialFunctions, self).accept()
