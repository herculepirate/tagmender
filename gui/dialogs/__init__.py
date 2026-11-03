"""gui/dialogs - Picard's small dialogs, as sheets of the main window.

  tags_from_filenames   Tags from File Names: a pattern such as "%artist% - %album%/%tracknumber% %title%",
                        a preview of what each file name gives, then the tags become plan overrides
                        (nothing is written until the plan is applied)
  info                  File Info: format, codec, bitrate, length, every tag as stored, embedded pictures
                        (read on a worker; read-only)
  logs                  The log viewer (tagmender.log: level, search, follow) and a job's details from
                        its journal and plan, with Undo through the executor's own undo

Each module has one function the window calls: show_tags_from_filenames, show_file_info, show_logs,
show_job_details."""
from .info import FileInfo, read_info, show_file_info  # noqa: F401
from .logs import JobDetails, LogViewer, job_details, show_job_details, show_logs  # noqa: F401
from .tags_from_filenames import (PRESETS, TagMatchExpression, TagsFromFileNames, apply_in_background,  # noqa: F401
                                  apply_to_plan, merge_into_store, merged_edit, parse, plan_files,
                                  show_tags_from_filenames)
