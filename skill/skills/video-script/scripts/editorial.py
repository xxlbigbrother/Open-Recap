"""CLI: reference-guided second-stage authoring from existing understanding."""
import argparse
import json
from editorial_runner import run_project


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--work-dir',required=True)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--candidate',help='Agent-authored story: validate and review once, never silently rewrite it')
    args=parser.parse_args()
    result=run_project(args.project,args.work_dir,prepare_only=args.prepare_only,force=args.force,candidate_path=args.candidate)
    print(json.dumps({k:v for k,v in result.items() if k not in {'attempts','output_hashes'}},ensure_ascii=False,indent=2))
    return 0 if result['status'] in {'prepared','ready_for_editorial_review'} else 2


if __name__=='__main__':raise SystemExit(main())
