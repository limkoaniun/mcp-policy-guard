# The upload-channel subset

Symlinks to the eight upload-borne scenarios under `attacks/scenarios/`
(A-01..A-04 and their benign twins), so a campaign can be pointed at this
channel alone with `--scenarios attacks/scenarios-upload` without re-running
the sixteen original subject-description scenarios. Nothing else reads this
directory; the suite of record is still `attacks/scenarios/`.
