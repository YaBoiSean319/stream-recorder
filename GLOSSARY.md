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

**Recording title**:
The editable human-readable name of a recording, distinct from its stable identity and capture dates.
_Avoid_: Recording ID

**Application branding**:
The shared name, visual identity, and appearance used to present this recorder to its users.
_Avoid_: User profile

**YouTube connection**:
The shared authorization linking the recorder to one YouTube channel for uploads.
_Avoid_: SSO account

**Upload job**:
A tracked attempt to transfer a completed recording to the connected YouTube channel with chosen video metadata and visibility.
_Avoid_: Recording, capture session
