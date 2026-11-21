"""Single place that registers every CLI command module."""
from . import (approval, attribution, cli, config, detect, doctor, gates, github_settings,
               guardrails, images, pack_tools, packs, quality, review, risky, sarif_convert,
               setup_answers, status, template, trace, upstream, vendoring, verify)


def register_all() -> None:
    """Register all commands. Safe to call more than once."""
    if not cli.is_registered("config check"):
        config.register_commands()
    if not cli.is_registered("config write"):
        setup_answers.register_commands()
    if not cli.is_registered("detect"):
        detect.register_commands()
    if not cli.is_registered("status"):
        status.register_commands()
    if not cli.is_registered("check attribution"):
        attribution.register_commands()
    if not cli.is_registered("check approval"):
        approval.register_commands()
    if not cli.is_registered("template apply"):
        template.register_commands()
    if not cli.is_registered("github plan"):
        github_settings.register_commands()
    if not cli.is_registered("doctor"):
        doctor.register_commands()
    if not cli.is_registered("upstream check"):
        upstream.register_commands()
    if not cli.is_registered("upstream verify"):
        vendoring.register_commands()
    if not cli.is_registered("guardrails check"):
        guardrails.register_commands()
    if not cli.is_registered("risky"):
        risky.register_commands()
    if not cli.is_registered("review gate"):
        review.register_commands()
    if not cli.is_registered("quality check"):
        quality.register_commands()
    if not cli.is_registered("quality sarif"):
        sarif_convert.register_commands()
    if not cli.is_registered("verify"):
        verify.register_commands()
    if not cli.is_registered("pack list"):
        packs.register_commands()
    if not cli.is_registered("pack check"):
        pack_tools.register_commands()
    if not cli.is_registered("trace"):
        trace.register_commands()
    if not cli.is_registered("gate prepare"):
        gates.register_commands()
    if not cli.is_registered("image args"):
        images.register_commands()
