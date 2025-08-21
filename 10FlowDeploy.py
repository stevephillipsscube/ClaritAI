# 11FlowDeploy.py
import argparse, shutil, subprocess, sys, io
from pathlib import Path

# --- UTF-8-safe console on Windows ---
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

FLOWS_DIR_DEFAULT = Path(r"force-app\main\default\flows")

def find_sf_cli() -> str:
    return shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"

def pick_latest_flow(flows_dir: Path) -> str:
    """Return Flow API name (file stem) of the newest Milestone_Emails_*.flow-meta.xml."""
    candidates = sorted(
        flows_dir.glob("Milestone_Emails_*.flow-meta.xml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No Milestone_Emails_*.flow-meta.xml found in {flows_dir.resolve()}")
    # Strip the full suffix ".flow-meta.xml" to get the Flow API name
    return candidates[0].name[:-len(".flow-meta.xml")]


def deploy_flow(source_paths: list[Path], org_alias: str, dry_run: bool):
    cli = find_sf_cli()
    cmd = [cli, "project", "deploy", "start"]
    for p in source_paths:
        cmd += ["--source-dir", str(p)]
    cmd += ["--target-org", org_alias]
    if dry_run:
        cmd.append("--dry-run")
    print("[INFO] Running:", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")



def main():
    ap = argparse.ArgumentParser(description="Deploy a Flow to Clariti via SF CLI.")
    ap.add_argument("--flows-dir", default=str(FLOWS_DIR_DEFAULT),
                    help="Folder containing *.flow-meta.xml (default: force-app/main/default/flows)")
    ap.add_argument("--flow-name", default="", help="Flow API name or filename (extension optional)")
    ap.add_argument("--org", default="clarit-org", help="sf CLI org alias (default: clarit-org)")
    ap.add_argument("--with-subflow", action="store_true",
                help="Also deploy Email_Autolaunched_Flow")

    ap.add_argument("--dry-run", action="store_true", help="Do a check-only deploy")
    args = ap.parse_args()

    flows_dir = Path(args.flows_dir)
    

    # Normalize flow name input (accept with or without suffix)
    if args.flow_name.strip():
        fn = args.flow_name.strip()
        lower = fn.lower()
        if lower.endswith(".flow-meta.xml"):
            flow_name = fn[:-len(".flow-meta.xml")]
        elif lower.endswith(".flow-meta"):
            flow_name = fn[:-len(".flow-meta")]
        elif lower.endswith(".xml"):
            flow_name = fn[:-len(".xml")]
        else:
            flow_name = fn
    else:
        flow_name = pick_latest_flow(flows_dir)

    flow_file = flows_dir / f"{flow_name}.flow-meta.xml"
    if not flow_file.exists():
        nearby = sorted(p.name for p in flows_dir.glob("*.flow-meta.xml"))
        raise SystemExit(f"Flow file not found: {flow_file}\nAvailable flows:\n  " + "\n  ".join(nearby))

    # Optionally include the subflow file
    source_paths = [flow_file]
    if getattr(args, "with_subflow", False):
        subflow_file = flows_dir / "Email_Autolaunched_Flow.flow-meta.xml"
        if subflow_file.exists():
            source_paths.append(subflow_file)
        else:
            print(f"[WARN] Subflow not found: {subflow_file} (skipping)")

    print(f"[INFO] Deploying: {', '.join(p.name for p in source_paths)} -> org {args.org}")
    res = deploy_flow(source_paths, args.org, args.dry_run)

    print(res.stdout)
    if res.returncode == 0:
        print("[✅] Flow deployment successful.")
    else:
        print("[❌] Deployment failed:")
        print(res.stderr)
        sys.exit(res.returncode)




if __name__ == "__main__":
    main()
