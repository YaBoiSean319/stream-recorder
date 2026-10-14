# Stream Recorder

Stream Recorder captures a chosen live HLS stream into retained recordings.

## Language

**Stream source**:
The configured direct HLS playlist and its access information for future captures and live preview.
_Avoid_: Website, channel, default site

**Recording**:
A single admitted capture session and the footage or final MP4 associated with it, including interruptions and recovery.
_Avoid_: Clip, download job

**Administrator**:
A person authorized to operate the recorder and manage shared configuration, access, and deliberate recording deletion when built-in authentication is enabled.
_Avoid_: Superuser, owner

**Operator**:
A person authorized to preview, capture, stop, list, download, and pin recordings when built-in authentication is enabled.
_Avoid_: Viewer, technician
