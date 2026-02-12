#!/usr/bin/env python3
"""
Init container script for ClawBench Docker setup.

Reads SCENARIO and VARIANT from environment variables, loads the scenario
YAML, and copies the correct AGENTS.md variant + workspace files into the
shared /workspace volume.

This replaces the workspace-setup portion of setup_scenario.py for Docker
use, eliminating the need to run Python on the host before docker compose up.
"""

import os
import shutil
import sys
from pathlib import Path

import yaml

# Paths inside the init container (mapped via docker-compose volumes)
SCENARIOS_DIR = Path("/scenarios")
FIXTURES_DIR = Path("/fixtures")
WORKSPACE_DIR = Path("/workspace")


def main():
    scenario_name = os.environ.get("SCENARIO", "client_escalation")
    variant = os.environ.get("VARIANT", "optimized")

    print(f"[init] Setting up workspace: scenario={scenario_name}, variant={variant}")

    # Load scenario YAML
    scenario_path = SCENARIOS_DIR / f"{scenario_name}.yaml"
    if not scenario_path.exists():
        available = [p.stem for p in SCENARIOS_DIR.glob("*.yaml")]
        print(f"[init] ERROR: Scenario not found: {scenario_name}")
        print(f"[init] Available: {available}")
        sys.exit(1)

    with open(scenario_path) as f:
        scenario = yaml.safe_load(f)

    # Ensure workspace dir exists
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # Copy AGENTS.md variant
    variants = scenario.get("variants", {})
    if variant not in variants:
        print(f"[init] ERROR: Unknown variant '{variant}'")
        print(f"[init] Available variants: {list(variants.keys())}")
        sys.exit(1)

    agents_src = FIXTURES_DIR / scenario_name / variants[variant]
    if not agents_src.exists():
        print(f"[init] ERROR: AGENTS.md variant not found: {agents_src}")
        sys.exit(1)

    shutil.copy2(agents_src, WORKSPACE_DIR / "AGENTS.md")
    print(f"[init] Copied {agents_src.name} -> /workspace/AGENTS.md")

    # Copy workspace files (USER.md, etc.)
    for dest_name, src_name in scenario.get("workspace", {}).items():
        src = FIXTURES_DIR / scenario_name / src_name
        if src.exists():
            shutil.copy2(src, WORKSPACE_DIR / dest_name)
            print(f"[init] Copied {src_name} -> /workspace/{dest_name}")
        else:
            print(f"[init] WARNING: Workspace file not found: {src}")

    print(f"[init] Workspace ready for scenario '{scenario_name}' (variant: {variant})")


if __name__ == "__main__":
    main()
