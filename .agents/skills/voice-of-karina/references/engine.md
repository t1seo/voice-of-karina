# Private engine transport

The skill's `scripts/run-engine.sh` resolves symlinks and finds its repository.
Invoke its absolute path from the loaded skill location. `uv run --project ...
--extra local` bootstraps the pinned local dependencies when first used. `uv` and
`ffmpeg` must already be available. The underlying command is
`uv run --project PROJECT_ROOT --extra local python -m voice_of_karina`.

The launcher uses `PROJECT_ROOT/.voice-of-karina` for saved jobs and profiles from
every current directory. A user-specified `VOICE_OF_KARINA_HOME` overrides this
location. Preserve that environment setting when resuming a job.

Write JSON using the agent's structured file tool, then pass `--request /absolute/path/request.json`.
Alternatively send exactly one JSON object on stdin. The response is one JSON
object on stdout; progress and model logs go to stderr. Never build shell source
from the message, URL, or video transcript. Resolve local audio paths to absolute
paths before writing the request.

Message IDs use ASCII letters, digits, `_`, or `-`, beginning with a letter or
digit. Keep them unique within a request. One request accepts up to 8 sources and
32 nonempty messages. Default language is Korean. Use the exact field names below;
unknown fields are rejected rather than silently ignored.

## Request recipes

### Analyze one or more references

```json
{"action":"analyze","sources":["https://www.youtube.com/watch?v=r96zEiIHVf4"],"language":"Korean"}
```

An explicit interval can be passed as `"source_time":{"source_index":0,
"start_seconds":12.5,"end_seconds":22.0}`. The zero-based index selects one of
the supplied sources. The interval still undergoes speech-quality checks.
Use real returned candidate IDs; the examples below are placeholders.

### Generate from a selected candidate

```json
{"action":"generate","mode":"mimic","analysis_job_id":"RETURNED_ANALYSIS_JOB_ID","candidate_id":"RETURNED_CANDIDATE_ID","messages":[{"id":"done","text":"작업이 끝났어요. 확인해 주세요."}],"profile_name":"카리나 참고 목소리","language":"Korean","max_attempts":2}
```

For a user-selected interval, a mimic generation can instead include `sources`
and `source_time`. Never provide both a timestamp and a candidate ID.

### Design an original voice

```json
{"action":"generate","mode":"design","voice_description":"따뜻하고 차분한 한국어 여성 목소리. 또렷하고 편안한 말투.","messages":[{"id":"rest","text":"잠시 쉬어 가셔도 괜찮아요."}],"profile_name":"차분한 안내 목소리","language":"Korean","max_attempts":2}
```

### Reuse or list saved voices

```json
{"action":"voices"}
```

```json
{"action":"generate","mode":"reuse","voice_id":"RETURNED_VOICE_ID","messages":[{"id":"next","text":"다음 작업도 준비됐어요."}],"language":"Korean"}
```

### Check or resume an interrupted job

```json
{"action":"status","job_id":"RETURNED_JOB_ID"}
```

```json
{"action":"resume","job_id":"RETURNED_JOB_ID"}
```

When a saved generation is waiting for a candidate choice, include the returned
`candidate_id` in `resume`. A changed message or voice is a new request. Resume
preserves the original message list and total attempt budget.

### Apply a completion sound, only when requested

```json
{"action":"install","job_id":"COMPLETED_JOB_ID","message_id":"done","target":"both"}
```

`target` is `claude`, `codex`, or `both`. Installation accepts an accepted artifact
from a saved job; arbitrary filenames are not transport inputs. It copies a
stdlib-only player and WAV into the selected tool home, preserves existing
settings, chains an unrelated existing Codex notifier, and returns backup paths.
Only this project's known legacy player command is retired during explicit
installation, so the old and new sounds do not play together.

## Quality and failure handling

Read the result's `status`, current step, input request, message results, attempts,
events, and errors. `complete` requires every requested message to be accepted;
`partial` is not full success. Link existing successful WAVs even if another line
failed, and explain which line still needs attention.

Default attempts: **2 total generations per message**, maximum **3**. Resume
does not grant more attempts. The check covers valid audible output, clipping,
duration, and ASR text comparison when available; it does not measure celebrity
similarity. Never relabel missing ASR or uncertain wording as a verified match.

For unsupported hardware, missing tools, inaccessible sources, a model download
failure, or exhausted attempts, report the specific actionable error. Do not
switch to an external API without a separately implemented and requested route.
