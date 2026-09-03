"""What both campaign CLIs need before any run happens.

`preflight`'s job is to fail honestly about the daemon, and the last thing
this project wants is two copies of that.
"""

from __future__ import annotations

from .ollama import model_fingerprint, warm_up


def preflight(models, fingerprint=None, warm=None) -> dict:
    """Check every model is reachable and pulled, then warm each one.

    Checked once, before any run. A campaign that cannot reach its models must
    fail here rather than produce a matrix of provider errors, each recording
    a failure that says nothing about the experiment.

    Warming follows A's §7: one candidate's first response after a load
    differed from the two that followed, and a measured run should not be the
    one that is different for a reason unrelated to the experiment.
    """
    fingerprint = fingerprint or model_fingerprint
    warm = warm or warm_up
    fingerprints = {}
    for model in models:
        try:
            taken = fingerprint(model)
        except OSError as error:
            raise RuntimeError(
                f"could not reach the Ollama daemon while checking {model!r}: "
                f"{error}. Start it with `ollama serve`. Checked before any run "
                f"so a campaign fails here rather than producing a matrix of "
                f"provider errors."
            ) from error
        if taken.get("digest", "unknown") == "unknown":
            raise RuntimeError(
                f"no Ollama model {model!r} is available -- start the daemon "
                f"with `ollama serve` and `ollama pull {model}`. Checked before "
                f"any run so a campaign fails here rather than producing a "
                f"matrix of provider errors."
            )
        warm(model)
        fingerprints[model] = taken
    return fingerprints


def warn_on_digest_drift(stored: dict, observed: dict) -> None:
    """Say so when a model's weights changed under a resumed campaign.

    The manifest keeps the digest it was created with, which is right --
    re-reading one must not invalidate a resume. But runs made after a
    re-pull would then be stamped with the old digest, which is a provenance
    claim that is simply false. Warning is what keeps the artifact honest
    without breaking the resume.
    """
    for model, taken in sorted(observed.items()):
        was = (stored.get(model) or {}).get("digest")
        now = taken.get("digest")
        if was and now and was != now:
            print(
                f"WARNING: {model!r} now reports digest {now}, but this campaign "
                f"was started with {was}. Runs made from here carry the stored "
                f"digest in agent.json and their provenance is therefore wrong. "
                f"Consider starting a new campaign."
            )
