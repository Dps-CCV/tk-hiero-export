# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import ast
import sys
import shutil
import tempfile
import re
import random
import string
import time

from hiero.exporters import FnCopyExporter
from hiero.exporters import FnCopyExporterUI

import hiero
from hiero.core import *
import hiero.core.nuke as nuke

import tank
import sgtk.util
from sgtk.platform.qt import QtGui, QtCore

import _nuke

from .base import ShotgunHieroObjectBase
from .collating_exporter import CollatingExporter, CollatedShotPreset

from . import (
    HieroGetQuicktimeSettings,
    HieroGetShot,
    HieroUpdateVersionData,
    HieroGetExtraPublishData,
    HieroPostVersionCreation,
)


####Copy Exporter
class ShotgunCopyExporterUI(ShotgunHieroObjectBase, FnCopyExporterUI.CopyExporterUI):
    """
    Custom Preferences UI for the shotgun transcoder

    Embeds the UI for the std transcoder UI.
    """

    def __init__(self, preset):
        FnCopyExporterUI.CopyExporterUI.__init__(self, preset)
        self._displayName = "Shotgun Copy Images"
        self._taskType = ShotgunCopyExporter

    def create_version_changed(self, state):
        create_version = (state == QtCore.Qt.Checked)
        self._preset._properties["create_version"] = create_version

    def populateUI(self, widget, exportTemplate):

        if self.app.get_nuke_version_tuple() >= (10, 5, 1):
            layout = widget.layout()
        else:
            layout = QtGui.QVBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(9)

        top = QtGui.QWidget()

        top_layout = QtGui.QVBoxLayout()
        top_layout.setContentsMargins(9, 0, 9, 0)
        create_version_checkbox = QtGui.QCheckBox("Create Shotgun Version", widget)
        create_version_checkbox.setToolTip(
            "Create a Version in Shotgun for this transcode.\n\n"
            "If the output format is not a quicktime, then\n"
            "a quicktime will be created.  The quicktime will\n"
            "be uploaded to Shotgun as Screening Room media."
        )

        create_version_checkbox.setCheckState(QtCore.Qt.Checked)
        if not self._preset._properties.get("create_version", True):
            create_version_checkbox.setCheckState(QtCore.Qt.Unchecked)
        create_version_checkbox.stateChanged.connect(self.create_version_changed)
        top_layout.addWidget(create_version_checkbox)

        top.setLayout(top_layout)

        middle = QtGui.QWidget()

        if self.app.get_nuke_version_tuple() >= (10, 5, 1):
            middle.setLayout(QtGui.QVBoxLayout())

        FnCopyExporterUI.CopyExporterUI.populateUI(self, middle, exportTemplate)

        layout.addWidget(top)
        layout.addWidget(middle)

        custom_widget = self._get_custom_widget(
            parent=widget,
            create_method="create_transcode_exporter_widget",
            get_method="get_transcode_exporter_ui_properties",
            set_method="set_transcode_exporter_ui_properties",
        )

        if custom_widget is not None:
            layout.addWidget(custom_widget)


class ShotgunCopyExporter(ShotgunHieroObjectBase, FnCopyExporter.CopyExporter, CollatingExporter):
    """
    Create Transcode object and send to Shotgun
    """

    _write_set_node_label = "SG_Write_Attachment"

    def __init__(self, initDict):
        """ Constructor """
        FnCopyExporter.CopyExporter.__init__(self, initDict)
        CollatingExporter.__init__(self)
        self._resolved_export_path = None
        self._sequence_name = None
        self._shot_name = None
        self._thumbnail = None

        randomName = ''.join(random.choices(string.ascii_lowercase, k=5))
        baseName = randomName + 'preview.mov'
        self._quicktime_path = os.path.join("C:\\TEMP_HIERO", baseName)
        if not os.path.exists("C:\\TEMP_HIERO\\"):
            os.makedirs("C:\\TEMP_HIERO\\")

        self._nuke_process     = None
        self._sg_version       = None
        self._quicktime_output = None

    ###EFECTOSCOPIO funciones para configuración de campos de metadata y expresiones

    EXPR_PATTERN = re.compile(r"\{([^}]+)\}(?:\[(.*?)\])?")

    def resolve_variable(self, meta, varname: str) -> str:
        try:
            v = meta.value('media.' + varname)
            if v:
                return str(v)
        except Exception:
            pass
        try:
            v = meta.value(varname)
            if v:
                return str(v)
        except Exception:
            pass
        return ""

    def parse_slice(self, token: str, value: str) -> str:
        if ":" in token:
            a, b = token.split(":", 1)
            start = int(a) if a.strip() else None
            end = int(b) if b.strip() else None
            return value[slice(start, end)]
        return value[int(token):] if token.startswith("-") else value[:int(token)]

    def apply_transform(self, value: str, token: str) -> str:
        t = token.strip()

        if t in ("", "trim", "strip"):
            return value.strip()
        if t == "ltrim": return value.lstrip()
        if t == "rtrim": return value.rstrip()
        if t == "lower": return value.lower()
        if t == "upper": return value.upper()
        if t == "title": return value.title()
        if t == "capitalize": return value.capitalize()

        if t.startswith("replace:"):
            _, old, new = t.split(":", 2)
            return value.replace(old, new)

        if t.startswith("remove:"):
            _, sub = t.split(":", 1)
            return value.replace(sub, "")

        if t.startswith("split:"):
            _, sep, idx = t.split(":", 2)
            parts = value.split(sep)
            return parts[int(idx)] if 0 <= int(idx) < len(parts) else ""

        if t.startswith("join:"):
            _, sep = t.split(":", 1)
            return sep.join(value)

        if ":" in t or t.lstrip("-").isdigit():
            return self.parse_slice(t, value)

        return value

    def evaluate_expression(self, meta, text: str) -> str:
        def repl(match):
            varname = match.group(1)
            pipeline = match.group(2)

            value = self.resolve_variable(meta, varname)

            if pipeline:
                for tok in pipeline.split("|"):
                    value = self.apply_transform(value, tok)

            return value

        return self.EXPR_PATTERN.sub(repl, text)

    def sequenceName(self):
        """override default sequenceName() to handle collated shots"""
        try:
            if self.isCollated():
                return self._parentSequence.name()
            else:
                return FnCopyExporter.CopyExporter.sequenceName(self)
        except AttributeError:
            return FnCopyExporter.CopyExporter.sequenceName(self)

    def startTask(self):
        if self._preset.properties()['create_version']:
            clip  = self._clip
            start = self._clip.sourceIn()
            end   = self._clip.sourceOut()

            fps = None
            if self._sequence:
                fps = self._sequence.framerate()
            if self._clip.framerate().isValid():
                fps = self._clip.framerate()

            script = nuke.ScriptWriter()
            self._script = script

            clip.addToNukeScript(script)
            rootNode = nuke.RootNode(start, end, fps)
            rootNode.setKnob("project_directory", os.path.split(self.resolvedExportPath())[0])
            rootNode.addProjectSettings(self._projectSettings)
            script.addNode(rootNode)

            clipReadNode  = clip.readNode()
            readFileName  = self._source.fileinfos()[0].filename()
            scriptReadNode = nuke.ReadNode(readFileName)

            _Clip_readNodeKnobsToIgnore = set((
                'name', 'file', 'width', 'height', 'pixelAspect',
                'first', 'last', 'localizationPolicy',
            ))

            knobsScript = clipReadNode.writeKnobs(
                _nuke.TO_SCRIPT | _nuke.WRITE_NON_DEFAULT_ONLY).split('\n')
            for knobScript in knobsScript:
                try:
                    firstSpace = knobScript.index(' ')
                    name = knobScript[:firstSpace]
                    if name not in _Clip_readNodeKnobsToIgnore:
                        value = knobScript[firstSpace + 1:]
                        scriptReadNode.setKnob(name, value)
                except ValueError:
                    continue

            if self._cutHandles is not None:
                handles = self._cutHandles
                if self._retime:
                    handles *= abs(self._item.playbackSpeed())

                sourceInOut = (self._item.sourceIn(), self._item.sourceOut())
                start = min(sourceInOut)
                end   = max(sourceInOut)

                start += self._clip.sourceIn()
                end   += self._clip.sourceIn()

                start = max(start - handles, self._clip.sourceIn())
                end   = min(end   + handles, self._clip.sourceOut())

            import math
            start = int(math.floor(start))
            end   = int(math.ceil(end))

            scriptReadNode.setKnob("file",  self._source.fileinfos()[0].filename())
            scriptReadNode.setKnob("first", start)
            scriptReadNode.setKnob("last",  end)
            script.addNode(scriptReadNode)

            reformatNode = nuke.ReformatNode(resize="fit", format="UHD_4k")
            script.addNode(reformatNode)

            writeNodeOutput = self._quicktime_path
            writeNode = nuke.WriteNode(writeNodeOutput)
            writeNode.setKnob("first",               start)
            writeNode.setKnob("last",                end)
            writeNode.setKnob("use_limit",           1)
            writeNode.setKnob("file_type",           "mov")
            writeNode.setKnob("codec",               "h264")
            writeNode.setKnob("mov64_fps",           fps)
            writeNode.setKnob("mov_h264_codec_profile", 1)
            writeNode.setKnob("mov64_quality",       "Low")
            writeNode.setKnob("colorspace",          "Output - Rec.709")
            script.addNode(writeNode)

            scriptPath  = os.path.join(tempfile.mkdtemp(), 'script.nk')
            script.writeToDisk(scriptPath)

            if not os.path.exists(scriptPath):
                print("Failed to write %s" % scriptPath)
            else:
                print("Successfully wrote %s. Executing now..." % scriptPath)
                sys.stdout.flush()

                logFileName = os.path.join(tempfile.mkdtemp(), 'script.log')
                self._nuke_process = nuke.executeNukeScript(scriptPath, open(logFileName, 'w'))
                self._quicktime_output = writeNodeOutput

                def poll():
                    returnCode = self._nuke_process.poll()
                    if returnCode is None:
                        QtCore.QTimer.singleShot(100, poll)
                    else:
                        if returnCode == 0 and os.path.exists(writeNodeOutput):
                            print("{} successfully rendered".format(writeNodeOutput))
                        else:
                            print("Nuke render failed (return code {})".format(returnCode))

                poll()

        if self._resolved_export_path is None:
            self._resolved_export_path = self.resolvedExportPath()
            self._tk_version      = self._formatTkVersionString(self.versionString())
            self._sequence_name   = self.sequenceName()
            self._resolved_export_path = self._resolved_export_path.replace("/", os.path.sep)

        if self.app.shot_count == 0:
            self.app.preprocess_data = {}

        if self.isCollated() and not self.isHero():
            item = self.heroItem()
        else:
            item = self._item

        self._sg_shot = self.app.execute_hook(
            "hook_get_shot",
            task=self,
            item=item,
            data=self.app.preprocess_data,
            fields=["sg_head_in", "sg_tail_out", "sg_cut_in", "sg_cut_out"],
            base_class=HieroGetShot,
        )

        self._sg_task = None
        try:
            if '_VREF_' in os.path.basename(self._resolved_export_path):
                tasks = self.app.shotgun.find(
                    "Task",
                    [['step.Step.code', 'is', 'VREF'],
                     ["entity", "is", self._sg_shot]],
                    ['content'])
            else:
                tasks = self.app.shotgun.find(
                    "Task",
                    [['step.Step.code', 'is', 'EDITORIAL'],
                     ["entity", "is", self._sg_shot]],
                    ['content'])
            if len(tasks) > 0:
                self._sg_task = tasks[0]
        except ValueError:
            setting = self.app.get_setting("default_task_filter", "[]")
            self.app.log_error("Invalid value for 'default_task_filter': %s" % setting)

        if self._preset.properties()['create_version']:
            sg_current_user = tank.util.get_current_user(self.app.tank)

            file_name = os.path.basename(self._resolved_export_path)
            file_name = os.path.splitext(file_name)[0]
            file_name = file_name.capitalize()

            if '_vref_' not in file_name:
                FileIn  = self._sg_shot["sg_head_in"]
                FileOut = self._sg_shot["sg_tail_out"]
            else:
                FileIn  = self._sg_shot["sg_cut_in"]
                FileOut = self._sg_shot["sg_cut_out"]

            self._version_data = {
                "user":              sg_current_user,
                "created_by":        sg_current_user,
                "entity":            self._sg_shot,
                "project":           self.app.context.project,
                "sg_path_to_frames": self._resolved_export_path,
                "code":              file_name,
                "sg_first_frame":    FileIn,
                "sg_last_frame":     FileOut,
                "frame_range":       "%s-%s" % (FileIn, FileOut),
                "sg_status_list":    "psu",
            }

            if self._sg_task is not None:
                self._version_data["sg_task"] = self._sg_task

            # DPS metadata inject
            if self._preset.properties().get("custom_sourceClip_bool_property", True):
                meta = self._item.source().mediaSource().metadata()
                sourceclip_name = self.evaluate_expression(
                    meta,
                    self._preset.properties().get("custom_sourceClip_text_property", ""))
                sg = self.app.shotgun
                filters = [
                    ["project", "is", self.app.context.project],
                    ["code",    "is", str(sourceclip_name)],
                ]
                sourceClip = sg.find_one("SourceClip", filters, ["code"])
                if not sourceClip:
                    sourceClip = sg.create("SourceClip", {
                        "code":    str(sourceclip_name),
                        "project": self.app.context.project,
                    })
                self._version_data['source_clip'] = sourceClip

            if self._preset.properties().get("custom_metadata_bool_property", True):
                if '_VREF_' not in os.path.basename(self._resolved_export_path):
                    try:
                        meta = self._item.source().mediaSource().metadata()

                        try:
                            sourceclip_name = self.evaluate_expression(
                                meta,
                                self._preset.properties().get("custom_sourceClip_text_property", ""))
                            sg = self.app.shotgun
                            filters = [
                                ["project", "is", self.app.context.project],
                                ["code",    "is", str(sourceclip_name)],
                            ]
                            sourceClip = sg.find_one("SourceClip", filters, ["code"])
                            if not sourceClip:
                                sourceClip = sg.create("SourceClip", {
                                    "code":    str(sourceclip_name),
                                    "project": self.app.context.project,
                                })
                            self._version_data['source_clip'] = sourceClip
                        except Exception:
                            self.app.log_info("Unable to gather sourceclip metadata")

                        if self._preset.properties().get("custom_metadata_focal_property", "") != "":
                            try:
                                focal = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_focal_property", ""))
                                self._version_data['sg_focal_length_metadata'] = focal
                            except Exception:
                                self.app.log_info("Unable to gather focal metadata")

                        if self._preset.properties().get("custom_metadata_iso_property", "") != "":
                            try:
                                iso = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_iso_property", ""))
                                self._version_data['sg_iso'] = iso
                            except Exception:
                                self.app.log_info("Unable to gather iso metadata")

                        if self._preset.properties().get("custom_metadata_wb_property", "") != "":
                            try:
                                wb = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_wb_property", ""))
                                self._version_data['sg_wb'] = wb
                            except Exception:
                                self.app.log_info("Unable to gather wb metadata")

                        if self._preset.properties().get("custom_metadata_camera_property", "") != "":
                            try:
                                camera = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_camera_property", ""))
                                self._version_data['sg_camera_model'] = camera
                            except Exception:
                                self.app.log_info("Unable to gather camera metadata")

                        if self._preset.properties().get("custom_metadata_shutter_property", "") != "":
                            try:
                                shutter = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_shutter_property", ""))
                                self._version_data['sg_shutter'] = shutter
                            except Exception:
                                self.app.log_info("Unable to gather shutter metadata")

                        if self._preset.properties().get("custom_metadata_tilt_property", "") != "":
                            try:
                                tilt = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_tilt_property", ""))
                                self._version_data['sg_tilt'] = tilt
                            except Exception:
                                self.app.log_info("Unable to gather tilt metadata")

                        if self._preset.properties().get("custom_metadata_roll_property", "") != "":
                            try:
                                roll = self.evaluate_expression(
                                    meta,
                                    self._preset.properties().get("custom_metadata_roll_property", ""))
                                self._version_data['sg_roll'] = roll
                            except Exception:
                                self.app.log_info("Unable to gather roll metadata")

                        width  = meta.value('media.input.width')
                        height = meta.value('media.input.height')
                        self._version_data['sg_width']  = int(width)
                        self._version_data['sg_height'] = int(height)

                    except Exception as e:
                        self.app.log_info(e)
                        self.app.log_info("Unable to retrieve metadata")

            self.app.execute_hook(
                "hook_update_version_data",
                version_data=self._version_data,
                task=self,
                base_class=HieroUpdateVersionData,
            )

        self._extra_publish_data = self.app.execute_hook(
            "hook_get_extra_publish_data",
            task=self,
            base_class=HieroGetExtraPublishData,
        )

        source = self._item.source()
        try:
            self._thumbnail = source.thumbnail(self._item.sourceIn())
        except Exception:
            pass

    def doFrame(self, src, dst):
        """ Run Task """
        dstdir = os.path.dirname(dst)
        util.filesystem.makeDirs(dstdir)

        self._tryCopy(src, dst)

        if self._currentPathIndex == len(self._paths) - 1:

            ctx = self.app.tank.context_from_entity('Shot', self._sg_shot['id'])

            if '_VREF_' in os.path.basename(self._resolved_export_path):
                published_file_type = self.app.get_setting("vref_published_file_type")
            elif '_PARAFX_' in os.path.basename(self._resolved_export_path):
                published_file_type = self.app.get_setting("parafx_published_file_type")
            else:
                published_file_type = self.app.get_setting("plate_published_file_type")

            basename  = os.path.splitext(os.path.basename(self._resolved_export_path))[0]
            if 'mov' in os.path.splitext(os.path.basename(self._resolved_export_path))[1]:
                finalName = '_'.join(basename.split('_')[:-1]) + '_mov'
            else:
                finalName = '_'.join(basename.split('_')[:-1])

            args = {
                "tk":                 self.app.tank,
                "context":            ctx,
                "path":               self._resolved_export_path,
                "name":               finalName,
                "version_number":     int(self._tk_version),
                "published_file_type": published_file_type,
            }

            if self._sg_task is not None:
                args["task"] = self._sg_task

            published_file_entity_type = sgtk.util.get_published_file_entity_type(self.app.sgtk)

            self.app.log_debug("Register publish in shotgun: %s" % str(args))
            pub_data = tank.util.register_publish(**args)

            if self._sg_task is not None:
                self.app.shotgun.update("Task", self._sg_task['id'],
                                        {"sg_status_list": "psu"})

            if self._extra_publish_data is not None:
                self.app.log_debug("Updating Shotgun %s %s" % (
                    published_file_entity_type, str(self._extra_publish_data)))
                self.app.shotgun.update(pub_data["type"], pub_data["id"],
                                        self._extra_publish_data)

            if self._thumbnail:
                self._upload_thumbnail_to_sg(pub_data, self._thumbnail)
            else:
                self.app.log_debug(
                    "There was no thumbnail available for %s %s" % (
                        published_file_entity_type, str(self._extra_publish_data)))

            vers = None
            if self._preset.properties()['create_version']:
                if published_file_entity_type == "PublishedFile":
                    self._version_data["published_files"] = [pub_data]
                else:
                    self._version_data["tank_published_file"] = pub_data

                self.app.log_debug("Creating Shotgun Version %s" % str(self._version_data))
                vers = self.app.shotgun.create("Version", self._version_data)

                # Store version for upload in finishTask()
                self._sg_version = vers

            if vers:
                self.app.execute_hook(
                    "hook_post_version_creation",
                    version_data=vers,
                    base_class=HieroPostVersionCreation,
                )

            if vers and hasattr(self, "_cut_item_data"):
                if "id" in self._cut_item_data:
                    cut_item_id = self._cut_item_data["id"]
                    self.app.shotgun.update("CutItem", cut_item_id, {"version": vers})
                    self.app.log_debug("Attached version to cut item.")
                    if self._thumbnail:
                        self._upload_thumbnail_to_sg(
                            {"type": "CutItem", "id": cut_item_id},
                            self._thumbnail)



        hiero.core.log.info("CopyExporter:")
        hiero.core.log.info("  - source: "      + str(src))
        hiero.core.log.info("  - destination: " + str(dst))

    def _wait_for_nuke(self):
        process = getattr(self, '_nuke_process', None)
        if process is None:
            return 0
        while process.poll() is None:
            time.sleep(0.1)
        return process.poll()

    def finishTask(self):
        return_code = self._wait_for_nuke()
        version     = getattr(self, '_sg_version', None)
        quicktime   = getattr(self, '_quicktime_path', None)

        if version is not None:
            if return_code == 0 and quicktime and os.path.exists(quicktime):
                try:
                    self.app.log_debug(
                        "Uploading quicktime to Shotgun... (%s)" % quicktime)
                    self.app.shotgun.upload(
                        "Version", version["id"], quicktime, "sg_uploaded_movie")
                except Exception as e:
                    self.app.log_warning("Quicktime upload failed: {}".format(e))
            else:
                self.app.log_debug(
                    "Quicktime not available (return_code={} path={})".format(
                        return_code, quicktime))
            if quicktime:
                try:
                    if os.path.exists(quicktime):
                        os.remove(quicktime)
                except Exception as e:
                    print(str(e))

        FnCopyExporter.CopyExporter.finishTask(self)
        CollatingExporter.finishTask(self)


class ShotgunCopyPreset(ShotgunHieroObjectBase, FnCopyExporter.CopyPreset, CollatedShotPreset):
    """ Settings for the shotgun transcode step """

    def __init__(self, name, properties):
        FnCopyExporter.CopyPreset.__init__(self, name, properties)
        self._parentType = ShotgunCopyExporter
        CollatedShotPreset.__init__(self, self.properties())

        self._properties["create_version"] = True

        custom_properties = self._get_custom_properties(
            "get_shot_processor_ui_properties"
        ) or []

        self.properties().update({d["name"]: d["value"] for d in custom_properties})