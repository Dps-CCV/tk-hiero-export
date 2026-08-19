# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import hiero.core
from hiero.exporters import FnShotExporter
import re
import os



from .base import ShotgunHieroObjectBase
from .collating_exporter import CollatingExporter

from . import (
    HieroGetShot,
    HieroUpdateShot,
    HieroUpdateCuts,
)


class ShotgunShotUpdater(
    ShotgunHieroObjectBase, CollatingExporter, FnShotExporter.ShotTask
):
    """
    Ensures that Shots and Sequences exist in Shotgun
    """

    def __init__(self, initDict):
        FnShotExporter.ShotTask.__init__(self, initDict)
        CollatingExporter.__init__(self)
        self._cut_order = None

    def _export_cdl_lmt_effects(self):
        import export_cdl_lmt

        track_item = self._item

        try:
            cdl_folder = os.path.join(os.path.dirname(os.environ['OCIO']), 'luts').replace('\\', '/')
        except Exception:
            cdl_folder = None
        try:
            lmt_folder = os.path.join(os.path.dirname(os.path.dirname(os.environ['OCIO'])), 'SHOW_LUT').replace('\\',
                                                                                                                '/')
        except Exception:
            lmt_folder = None

        if not cdl_folder and not lmt_folder:
            self.app.log_info("No CDL/LMT export folders configured — skipping.")
            return None, None

        cdl_name = None
        lmt_name = None

        # ── CDL ─────────────────────────────────────────────────────────────
        if cdl_folder:
            os.makedirs(cdl_folder, exist_ok=True)
            cdl_paths, skip = export_cdl_lmt.export_cdl_effects(
                track_item,
                cdl_folder,
                use_expr=True,
                cdl_expr="{clip}",
                overwrite=False,
                use_subfolder=False,
                subfolder_expr="",
            )
            self.app.log_info("CDL export: {} exported, {} skipped".format(
                len(cdl_paths), skip))

            if cdl_paths:
                # File was written — get name from actual output path
                cdl_name = os.path.splitext(os.path.basename(cdl_paths[0]))[0]
            else:
                # File was skipped (already exists) — resolve the name the same
                # way export_cdl_effects would have, so we still return it
                clip_name = track_item.name()
                cdl_name = clip_name

        # ── LMT ─────────────────────────────────────────────────────────────
        if lmt_folder:
            os.makedirs(lmt_folder, exist_ok=True)
            lmt_paths, skip = export_cdl_lmt.export_lmt_effects(
                track_item,
                lmt_folder,
                use_expr=False,
                lmt_expr="",
                overwrite=False,
            )
            self.app.log_info("LMT export: {} exported, {} skipped".format(
                len(lmt_paths), skip))

            if lmt_paths:
                # File was written — get name from actual output path
                lmt_name = os.path.splitext(os.path.basename(lmt_paths[0]))[0]
            else:
                # File was skipped — resolve the name from the node's file knob
                # since lmt_expr is empty and use_expr is False, the original
                # filename is retained, so read it directly from the effect node
                try:
                    for linked in track_item.linkedItems():
                        if (hasattr(linked, "node") and
                                linked.node().Class() == "OCIOFileTransform"):
                            src = linked.node().knob("file").value()
                            if src:
                                lmt_name = os.path.splitext(
                                    os.path.basename(src.replace("\\", "/")))[0]
                            break
                except Exception as e:
                    self.app.log_warning("Could not resolve LMT name: {}".format(e))

        self.app.log_info("CDL name: {}".format(cdl_name))
        self.app.log_info("LMT name: {}".format(lmt_name))

        return cdl_name, lmt_name

    # def _get_parafx_sibling_task(self):
    #     """
    #     Walk the submission tree and return the sibling task whose
    #     export path template contains 'PARAFX'. Returns None if not found.
    #     """
    #     try:
    #         def _iter_tasks(node):
    #             if hasattr(node, 'children'):
    #                 for child in node.children():
    #                     for t in _iter_tasks(child):
    #                         yield t
    #             else:
    #                 yield node
    #
    #         for task in _iter_tasks(self._submission):
    #             if task is self:
    #                 continue
    #             # Check the unresolved template — always contains PARAFX literally
    #             export_path = getattr(task, '_exportPath', '') or ''
    #             if 'PARAFX' in export_path:
    #                 return task
    #
    #     except Exception as e:
    #         self.app.log_info("_get_parafx_sibling_task error: {}".format(e))
    #
    #     return None
    ###EFECTOSCOPIO funciones para configuración de campos de metadata y expresiones


    EXPR_PATTERN = re.compile(r"\{([^}]+)\}(?:\[(.*?)\])?")

    def resolve_variable(self, meta, varname: str) -> str:
        # Replace this with your metadata logic
        return meta['media.' + varname]
        # if varname == "exr.width":
        #     return "A001C001_01"
        # if varname == "exr.h":
        #     return "AA"
        return f"<unknown:{varname}>"

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

        # re.sub replaces ALL matches, leaving the rest unchanged
        return self.EXPR_PATTERN.sub(repl, text)

    def _timecode(self, frame, fps, drop_frame=False):
        """Convenience wrapper to convert a given frame and fps to a timecode.

        :param frame: Frame number
        :param fps: Frames per seconds (float)
        :return: timecode string
        """

        if drop_frame:
            display_type = hiero.core.Timecode.kDisplayDropFrameTimecode
        else:
            display_type = hiero.core.Timecode.kDisplayTimecode

        return hiero.core.Timecode.timeToString(frame, fps, display_type)

    def get_cut_item_data(self):
        """
        Return some computed values for use when creating cut items.

        The values correspond to the exported version created on disk.
        """

        (head_in, tail_out) = self.collatedOutputRange(clampToSource=False)

        handles = self._cutHandles if self._cutHandles is not None else 0
        in_handle = handles
        out_handle = handles

        # get the frame offset specified in the export options
        startFrame = self._startFrame or 0

        # these are the source in/out frames. we'll use them to determine if we
        # have enough frames to account for the handles. versions of
        # hiero/nukestudio handle missing handles differently
        source_in = int(self._item.sourceIn())
        source_out = int(self._item.sourceOut())

        if self._has_nuke_backend() and source_in < in_handle:
            # newer versions of the hiero/nukestudio. no black frames will be
            # written to disk for the head when not enough source for the in
            # handle. the in/out should be correct. but the start handle is
            # limited by the in value. the source in point is within the
            # specified handles.
            in_handle = source_in

            # NOTE: even new versions of hiero/nukestudio will write black
            # frames for insuffient tail handles. so we don't need to account
            # for that case here.

        # "cut_length" is a boolean set on the updater by the shot processor.
        # it signifies whether the transcode task will write the cut length
        # to disk (True) or if it will write the full source to disk (False)
        if self.is_cut_length_export():
            cut_in = head_in + in_handle
            cut_out = tail_out - out_handle
        else:
            cut_in = source_in
            cut_out = source_out

            # account for any custom start frame
            cut_in += startFrame
            cut_out += startFrame

        # get the edit in/out points from the timeline
        edit_in = self._item.timelineIn()
        edit_out = self._item.timelineOut()

        # account for custom start code in the hiero timeline
        seq = self._item.sequence()
        edit_in += seq.timecodeStart()
        edit_out += seq.timecodeStart()

        cut_duration = cut_out - cut_in + 1
        edit_duration = edit_out - edit_in + 1

        if cut_duration != edit_duration:
            self.app.log_warning(
                "It looks like the shot %s has a retime applied. PTR cuts do "
                "not support retimes." % (self.clipName(),)
            )

        working_duration = tail_out - head_in + 1

        if not self._has_nuke_backend() and self.isCollated():
            # undo the offset that is automatically added when collating.
            # this is only required in older versions of hiero
            head_in -= self.HEAD_ROOM_OFFSET
            tail_out -= self.HEAD_ROOM_OFFSET

        # return the computed cut information
        hiero_sequence = self._item.sequence()

        # the sequence fps, used to calculate timecodes for cut items
        fps = hiero_sequence.framerate().toFloat()

        # get whether sequence timecode is displayed in drop frame format
        drop_frame = hiero_sequence.dropFrame()
        tc_cut_item_in = self._timecode((self._item.source().mediaSource().timecodeStart() + in_handle), fps, drop_frame)
        tc_cut_item_out = self._timecode((self._item.source().mediaSource().timecodeStart() + in_handle + cut_duration), fps,
                                         drop_frame)
        return {
            "cut_item_in": cut_in,
            "cut_item_out": cut_out,
            "cut_item_duration": cut_duration,
            "edit_in": edit_in,
            "edit_out": edit_out,
            "edit_duration": edit_duration,
            "head_in": head_in,
            "tail_out": tail_out,
            "working_duration": working_duration,
            "timecode_cut_item_in_text": tc_cut_item_in,
            "timecode_cut_item_out_text": tc_cut_item_out,
        }

    def finishTask(self):
        FnShotExporter.ShotTask.finishTask(self)
        CollatingExporter.finishTask(self)

    def taskStep(self):
        """
        Execution payload.
        """
        # Only process actual shots... so uncollated items and hero collated items
        if self.isCollated() and not self.isHero():
            return False

        # execute base class
        FnShotExporter.ShotTask.taskStep(self)

        # call the preprocess hook to get extra values
        if self.app.shot_count == 0:
            self.app.preprocess_data = {}

        sg_shot = self.app.execute_hook(
            "hook_get_shot",
            task=self,
            item=self._item,
            data=self.app.preprocess_data,
            base_class=HieroGetShot,
        )

        # clean up the dict
        shot_id = sg_shot["id"]
        del sg_shot["id"]
        shot_type = sg_shot["type"]
        del sg_shot["type"]

        # The cut order may have been set by the processor. Otherwise keep old behavior.
        cut_order = self.app.shot_count + 1
        if self._cut_order:
            cut_order = self._cut_order

        # update the frame range
        sg_shot["sg_cut_order"] = cut_order

        # get cut info
        cut_info = self.get_cut_item_data()

        head_in = cut_info["head_in"]
        tail_out = cut_info["tail_out"]
        cut_in = cut_info["cut_item_in"]
        cut_out = cut_info["cut_item_out"]
        cut_duration = cut_info["cut_item_duration"]
        working_duration = cut_info["working_duration"]

        self.app.log_debug("Head/Tail from Hiero: %s, %s" % (head_in, tail_out))

        if self.isCollated():

            if self.is_cut_length_export():
                # nothing to do here. the default calculation above is enough.
                self.app.log_debug("Exporting... collated, cut length.")

                # Log cut length collate metric
                try:
                    self.app.log_metric("Collate/Cut Length", log_version=True)
                except:
                    # ingore any errors. ex: metrics logging not supported
                    pass

            else:
                self.app.log_debug("Exporting... collated, clip length.")

                # NOTE: Hiero crashes when trying to collate with a
                # custom start frame. so this will only work for source start
                # frame.

                # the head/in out values should be the first and last frames of
                # the source, but they're not. they're actually the values we
                # expect for the cut in/out.
                cut_in = head_in
                cut_out = tail_out

                # ensure head/tail match the entire clip (clip length export)
                head_in = 0
                tail_out = self._clip.duration() - 1

                # get the frame offset specified in the export options
                start_frame = self._startFrame or 0

                # account for a custom start frame if/when clip length collate
                # works on custom start frame.
                head_in += start_frame
                tail_out += start_frame
                cut_in += start_frame
                cut_out += start_frame

                # since we've set the head/tail, recalculate the working
                # duration to make sure it is correct
                working_duration = tail_out - head_in + 1

                # since we've set the cut in/out, recalculate the cut duration
                # to make sure it is correct
                cut_duration = cut_out - cut_in + 1

                # Log clip length collate metric
                try:
                    self.app.log_metric("Collate/Clip Length", log_version=True)
                except:
                    # ingore any errors. ex: metrics logging not supported
                    pass

        else:
            # regular export. values we have are good. just log it
            if self.is_cut_length_export():
                self.app.log_debug("Exporting... cut length.")
            else:
                # the cut in/out should already be correct here. just log
                self.app.log_debug("Exporting... clip length.")

        # update the frame range
        sg_shot["sg_head_in"] = head_in
        sg_shot["sg_cut_in"] = cut_in
        sg_shot["sg_cut_out"] = cut_out
        sg_shot["sg_tail_out"] = tail_out
        sg_shot["sg_cut_duration"] = cut_duration
        sg_shot["sg_working_duration"] = working_duration

        # get status from the hiero tags
        status = None
        status_map = dict(self._preset.properties()["sg_status_hiero_tags"])
        for tag in self._item.tags():
            if tag.name() in status_map:
                status = status_map[tag.name()]
                break
        if status:
            sg_shot["sg_status_list"] = status

        # get task template from the tags
        template = None
        template_map = dict(self._preset.properties()["task_template_map"])
        for tag in self._item.tags():
            if tag.name() in template_map:
                template = self.app.tank.shotgun.find_one(
                    "TaskTemplate",
                    [
                        ["entity_type", "is", shot_type],
                        ["code", "is", template_map[tag.name()]],
                    ],
                )
                break

        # if there are no associated, assign default template...
        if template is None:
            default_template = self.app.get_setting("default_task_template")
            if default_template:
                template = self.app.tank.shotgun.find_one(
                    "TaskTemplate",
                    [
                        ["entity_type", "is", shot_type],
                        ["code", "is", default_template],
                    ],
                )

        if template is not None:
            sg_shot['task_template'] = template

        # DPS SourceClip export
        if self._preset.properties().get("custom_sourceClip_bool_property", True):
            meta = self._item.source().mediaSource().metadata()
            sourceclip_name = self.evaluate_expression(meta, self._preset.properties().get("custom_sourceClip_text_property", ""))
            sg = self.app.shotgun
            filters = [
                ["project", "is", self.app.context.project],
                ["code", "is", str(sourceclip_name)],
            ]
            sourceClip = sg.find_one("SourceClip", filters, ["code"])
            if not sourceClip:
                sourceclip_data = {
                    "code": str(sourceclip_name),
                    "project": self.app.context.project,
                }
                sourceClip = sg.create("SourceClip", sourceclip_data)

            sg_shot['sg_source_clip'] = sourceClip

        #DPS metadata extract

        # if self._preset.properties().get("custom_cdls_bool_property", True):
        if self._preset.properties().get("custom_cdls_bool_property", True):
            try:
                cdl, lmt = self._export_cdl_lmt_effects()
                if lmt != None:
                    sg_shot['sg_lmt'] = lmt
                if cdl != None:
                    sg_shot['sg_cdl'] = cdl
            except Exception as e:
                self.app.log_warning("CDL/LMT export failed for {}: {}".format(
                    self.shotName(), e))

            # if self._preset.properties().get("custom_metadata_lmt_property", "") != "":
            #     try:
            #         lmt = self.evaluate_expression(meta,
            #                                        self._preset.properties().get("custom_metadata_lmt_property", ""))
            #         sg_shot['sg_lmt'] = lmt
            #     except:
            #         self.app.log_info("Unable to gather lmt metadata")


        if self._preset.properties().get("custom_metadata_bool_property", True):
            try:
                meta = self._item.source().mediaSource().metadata()
                if self._preset.properties().get("custom_metadata_focal_property", "") != "":
                    try:
                        focal = self.evaluate_expression(meta,
                                                self._preset.properties().get("custom_metadata_focal_property", ""))
                        sg_shot['sg_focal_length_metadata'] = focal
                    except:
                        self.app.log_info("Unable to gather focal metadata")

                if self._preset.properties().get("custom_metadata_iso_property", "") != "":
                    try:
                        iso = self.evaluate_expression(meta,
                                                self._preset.properties().get("custom_metadata_iso_property", ""))
                        sg_shot['sg_iso'] = iso
                    except:
                        self.app.log_info("Unable to gather iso metadata")

                if self._preset.properties().get("custom_metadata_wb_property", "") != "":
                    try:
                        wb = self.evaluate_expression(meta,
                                              self._preset.properties().get("custom_metadata_wb_property", ""))
                        sg_shot['sg_wb'] = wb
                    except:
                        self.app.log_info("Unable to gather wb metadata")

                if self._preset.properties().get("custom_metadata_camera_property", "") != "":
                    try:
                        camera = self.evaluate_expression(meta,
                                              self._preset.properties().get("custom_metadata_camera_property", ""))
                        sg_shot['sg_camera_model'] = camera
                    except:
                        self.app.log_info("Unable to gather camera metadata")

                if self._preset.properties().get("custom_metadata_shutter_property", "") != "":
                    try:
                        shutter = self.evaluate_expression(meta,
                                              self._preset.properties().get("custom_metadata_shutter_property", ""))
                        sg_shot['sg_shutter'] = shutter
                    except:
                        self.app.log_info("Unable to gather shutter metadata")

                if self._preset.properties().get("custom_metadata_tilt_property", "") != "":
                    try:
                        tilt = self.evaluate_expression(meta,
                                              self._preset.properties().get("custom_metadata_tilt_property", ""))
                        sg_shot['sg_tilt'] = tilt
                    except:
                        self.app.log_info("Unable to gather tilt metadata")

                if self._preset.properties().get("custom_metadata_roll_property", "") != "":
                    try:
                        roll = self.evaluate_expression(meta,
                                              self._preset.properties().get("custom_metadata_roll_property", ""))
                        sg_shot['sg_roll'] = roll
                    except:
                        self.app.log_info("Unable to gather roll metadata")

                width = meta['media.input.width']
                height = meta['media.input.height']
                sg_shot['sg_width'] = int(width)
                sg_shot['sg_height'] = int(height)

            except Exception as e:
                self.app.log_info(e)
                self.app.log_info("Unable to retrieve metadata")

        # commit the changes and update the thumbnail
        self.app.execute_hook_method(
            "hook_update_shot",
            "update_shotgun_shot_entity",
            entity_type=shot_type,
            entity_id=shot_id,
            entity_data=sg_shot,
            preset_properties=self._preset.properties(),
            base_class=HieroUpdateShot,
        )

        # create the directory structure
        self.app.execute_hook_method(
            "hook_update_shot",
            "create_filesystem_structure",
            entity_type=shot_type,
            entity_id=shot_id,
            preset_properties=self._preset.properties(),
            base_class=HieroUpdateShot,
        )

        # return without error
        self.app.log_info("Updated %s %s" % (shot_type, self.shotName()))

        # keep shot count
        self.app.shot_count += 1

        # create the CutItem with the data populated by the shot processor
        cut = None

        if hasattr(self, "_cut_item_data"):
            cut_item_data = self._cut_item_data
            cut_item = self.app.execute_hook_method(
                "hook_update_cuts",
                "create_cut_item",
                cut_item_data=cut_item_data,
                preset_properties=self._preset.properties(),
                base_class=HieroUpdateCuts,
            )

            # If a CutItem entity wasn't created by the hook method, then it
            # will have returned a None.
            if cut_item is not None:
                # update the object's cut item data to include the new info
                self._cut_item_data.update(cut_item)

                cut = cut_item["cut"]

        # see if this task has been designated to update the Cut thumbnail
        if cut and hasattr(self, "_create_cut_thumbnail"):
            thumbnail = self.app.execute_hook_method(
                "hook_update_cuts",
                "get_cut_thumbnail",
                cut=cut,
                task_item=self._item,
                preset_properties=self._preset.properties(),
                base_class=HieroUpdateCuts,
            )

            if thumbnail:
                # found one, uplaod to sg for the cut
                self._upload_thumbnail_to_sg(cut, thumbnail)

        # return false to indicate success
        return False

    def is_cut_length_export(self):
        """
        Returns ``True`` if this task has the "Cut Length" option checked.

        This is set by the shot processor.
        """
        return hasattr(self, "_cut_length") and self._cut_length


class ShotgunShotUpdaterPreset(ShotgunHieroObjectBase, hiero.core.TaskPresetBase):
    """
    Settings preset
    """

    def __init__(self, name, properties):
        hiero.core.TaskPresetBase.__init__(self, ShotgunShotUpdater, name)
        self.properties().update(properties)

    def supportedItems(self):
        return hiero.core.TaskPresetBase.kAllItems
