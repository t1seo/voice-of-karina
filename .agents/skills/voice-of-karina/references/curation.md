# Prepare and reuse a reviewed voice

Use this reference for the packaged Karina sample voice, a new person's reference
comparison, or an exact local WAV. Read [engine.md](engine.md) for the transport,
ordinary generation, and output review. Keep IDs and sampling controls inside the
agent workflow; the user chooses a voice by listening and describing a preference.

## Reuse the reviewed Karina reference

When the user requests the voice used for the README's reviewed attention sample:

```json
{"action":"preset","preset_id":"karina-reviewed-v1","profile_name":"카리나 검토 참조"}
```

Use `voice.id` from the returned `ProfileResult`:

```json
{"action":"generate","mode":"reuse","voice_id":"RETURNED_VOICE_ID","messages":[{"id":"next","text":"다음 작업도 준비됐어요."}]}
```

The packaged native-rate PCM is the exact 3.051958-second reference with SHA-256
`22133aea2c959269cd804edfdd9bcb1b29945d80b9002782aecfcb15f25d898f` and transcript
“오늘 재밌는 시간 함께 보내봅시다”. Its saved recipe uses the pinned Qwen 1.7B
Base model and the reviewed experiment's seed and sampling settings. Registration
verifies and copies those bytes; it does not download YouTube or recrop a similar
three-second interval. Dependencies and ASR/TTS weights may still download on
first use. New text still needs its own output checks and listening judgment.

## Prepare a new person's voice

1. Run `analyze` on the supplied links or recording. Confirm the intended person
   only when ambiguous. Examine complete utterances, warnings, and transcripts.
   Two references must belong to the same confirmed person; multiple links do not
   establish that automatically.
2. Choose two useful candidates, optionally three. Include an original fallback
   when it provides a meaningful comparison. Do not choose solely by shortest
   duration, highest speech coverage, or ASR confidence.
3. Register each candidate. This preserves its exact crop and independently
   checks the stored audio and transcript before saving a copied profile.

```json
{"action":"register_candidate","analysis_job_id":"RETURNED_ANALYSIS_JOB_ID","candidate_id":"RETURNED_CANDIDATE_A","name":"요청한 목소리 후보 A"}
```

```json
{"action":"register_candidate","analysis_job_id":"RETURNED_ANALYSIS_JOB_ID","candidate_id":"RETURNED_CANDIDATE_B","name":"요청한 목소리 후보 B"}
```

Each response contains `voice`; use the real returned `voice.id` values. A profile
registered this way is a reference candidate, not a claimed listening approval.
When `recipe` is omitted, candidate registration records the current defaults
with the analysis language.
If only one usable candidate exists, or the user wants a quick one-off result,
use the ordinary selected-candidate generation path instead of manufacturing a
second candidate or blocking the request on a comparison.

## Compare a bounded set of previews

Use one requested message for the initial comparison. Include a second requested
message when it tests a useful difference, without adding an unsolicited pack.

```json
{"action":"audition","voice_ids":["RETURNED_VOICE_A","RETURNED_VOICE_B"],"messages":[{"id":"attention","text":"확인이 필요해요. 잠깐 봐 주세요."}],"language":"Korean"}
```

The defaults compare both references at seeds `20260920` and `20260921`: **four
actual generations**. Each reference/message/seed cell gets one synthesis call.
The request accepts 2–3 distinct profiles, 1–2 messages, and 1–2 distinct seeds;
the maximum is 12 trials. This is a finite study, separate from the ordinary
two-attempt output loop. No `max_attempts` field exists for auditions.

The saved result contains `job_id`, `request`, copied `profiles`, `trials`,
eligible `choices`, `status`, and any `selection`/`selected_voice_id`. Use the
returned trial audio and quality evidence to present playable choices. Do not
guess file paths or infer a successful trial from a filename.

For an interrupted `audition-...` job, use the same `status` and `resume` actions
as ordinary jobs. Completed trials remain recorded, and resuming does not enlarge
the original matrix. If the comparison fails, report the results and remaining
input needs. Do not launch fresh studies, change text, lower quality thresholds,
or alter settings merely to bypass exhausted trials.

## Select only an explicitly reviewed choice

A selectable choice combines one reference and one seed that passed **every
preview message** under the current quality policy. Its returned `choice_id`
binds the recipe and trial digests. This is eligibility for review, not a judgment
that it sounds natural or matches the intended person.

Offer a short question such as “A와 B를 들어 보시고 더 자연스러운 쪽을 골라 주세요.”
Label each returned choice separately, even when two choices share a reference,
and link all preview messages belonging to that choice.
Use the user's prior explicit preference if it unambiguously identifies these
files. If the agent has no listening capability, leave selection pending for the
user instead of inventing listening observations. Scores alone cannot supply the
reason for a claimed listening approval.

After the user or a capable reviewer makes an explicit, attributable choice:

```json
{"action":"select_voice","job_id":"RETURNED_AUDITION_JOB_ID","choice_id":"RETURNED_CHOICE_SHA256","name":"선택한 안내 목소리","reason":"The user listened to the linked previews and chose candidate A for its clearer pronunciation."}
```

Replace the example reason with the actual review and use the returned choice
digest. The result contains the selected `voice`. Selection creates an immutable,
reusable profile; replaying the same selection is idempotent. Do not rewrite the
trial manifest or submit a different digest as though it were the reviewed file.

Return the selected trial files when they already contain the requested messages.
For a new phrase, call ordinary `generate` in `reuse` mode with the selected
`voice.id` and recipe language, omitting `settings` so the stored recipe is
restored. Saving a profile does not approve later outputs; continue ordinary
validation and `reject → resume` when a later output is reported as poor.

Audition trial WAVs cannot be passed directly to `install` or the ordinary
`reject` action. If completion notification installation is requested, use the
selected profile to create a regular generation job, review its verified output,
then install that job's message. Explain this additional generation when needed.

## Preserve an exact local reference

Use `register` for an already chosen native-rate mono PCM WAV when it must be
copied without another crop. Resolve its path and calculate the actual SHA-256;
retain the accurate spoken transcript and provenance.

```json
{"action":"register","name":"검토한 로컬 목소리","reference":{"audio_path":"/absolute/path/reference.wav","sha256":"EXACT_REFERENCE_SHA256","transcript":"THE_WORDS_ACTUALLY_SPOKEN","provenance":{"kind":"mimic","source":"/absolute/path/original-recording.wav"},"preparation":["original"]}}
```

Registration checks a 3–15-second mono PCM WAV, its digest, signal quality, and a
fresh transcript. It does not fix noise, identify a speaker, or provide listening
approval. `recipe` is optional. Supply known generation conditions when available;
recording settings alone does not establish a successful or reviewed experiment.
Without a recipe this action checks Korean and saves a reference-only profile.
For another language, include a compatible recipe with that language explicitly.

## Saved recipes and reproducibility

`VoiceRecipe` has `version: 1`, `language`, `model_id`, `model_revision`, and
`settings`. Supported settings are `seed`, `temperature`, `top_p`, `top_k`, and
`repetition_penalty`. Current defaults are `20260920`, `0.9`, `1.0`, `50`, and
`1.5`. Ordinary retries use distinct deterministic seeds within the existing
attempt budget. The output's synthesis evidence records the applied settings,
text, runtime, and audio digest.

Audition `settings` can explicitly fix these controls; its `seeds` matrix supplies
the per-trial seed. Preserve a controlled study's inputs rather than silently
retuning after seeing failures. Older profiles with no recipe remain usable but
must not be described as comparison-reviewed. Fixed seeds make a documented run
repeatable under the same conditions; they do not promise identical sound across
different hardware or backend versions.
