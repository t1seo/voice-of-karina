# Publish Voice of Karina and both requested samples

## Scope and decisions

The user requested the existing task-complete sample and a new Karina-reference sample saying “오늘도 수고 많으셨어요. 잠깐 쉬었다가 다시 시작해 볼까요?”, playable in the README, followed by committing all intended project changes, pushing and merging into main. The prior rewrite has already passed 147 tests, full lint/type checks and independent reviews. This follow-up adds media and documentation; it does not change the speech engine.

Metis-style read-only gap review confirmed origin/main at a7928eaec70222924e9821601b35593ff4e37b1a, no open PR, no protection rule, and all normal merge methods available. No further user decision is needed. Runtime evidence and local voice/model data remain local; source, skill, tests, public docs, intended samples and legacy deletions are published.

## Execution and acceptance

1. Reuse the saved Karina profile through the real skill engine. Preserve exact requested wording; accept only an actual passing result. Copy its WAV, encode H.264/AAC MP4 using the unchanged photo, fully decode both and record hashes, actual ASR and per-sample generation time.
2. Upload the MP4 through the official GitHub user-attachment endpoint. Retain existing done/attention/ready media. Update English/Korean READMEs and the listening gallery to four Karina samples plus one original designed voice. Append provenance with relative paths and an explicit update time, retaining original generation metadata.
3. Ignore the entire .omo directory before staging. Check all public file paths and content for local runtime paths or credentials; verify photo hash, launcher mode 100755 and Claude link mode 120000. Run all configured local checks on the final staged project. There is no GitHub CI workflow; do not claim remote CI success.
4. Create a feature branch, explicitly stage intended project additions/modifications/deletions, inspect the staged diff, commit and push. Create a concrete PR describing the completed rewrite and actual validation. Merge the exact reviewed head SHA using an allowed normal merge method; never bypass a newly appearing check or protection rule.
5. Fast-forward local main to the actual merged remote commit. With a fresh browser profile, open the published English and Korean README on GitHub and play all four embedded videos. Verify advancing playback, decoded audio, loaded original photo and donation image. Capture sanitized evidence, close the QA tab/profile and confirm clean local main.

## Failure handling and cleanup

A generation failure uses the persisted bounded budget; do not fabricate or relabel a sample. A failed upload or playback prevents reporting publication complete. If main changes, fetch and integrate it without overwriting remote history, repeat only checks affected by the change, and merge only the reviewed head. Preserve existing unrelated user files/configuration. No model/server/browser process or signed temporary HTML should remain after QA; keep only deliberate local evidence and reusable voice data.

## Success

The requested phrases both have playable published README samples; all intended rewrite changes are committed and merged; local main matches origin/main with no unintended worktree change; the original Karina photo and donation button remain intact.
