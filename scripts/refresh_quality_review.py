"""Refresh predictions; retain human answers only for identical frozen records."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cdu_radar.async_cli import configuration
from cdu_radar.quality_benchmark import (load_package,export_package,rebase_labels,import_labels,evaluate_package)
from cdu_radar.featured_benchmark import save_new


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-directory',type=Path,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--analysis-state',type=Path,required=True)
    parser.add_argument('--analysis-config',type=Path,required=True)
    parser.add_argument('--labels-csv',type=Path,required=True)
    parser.add_argument('--labels-db',type=Path,required=True)
    parser.add_argument('--labeler',required=True)
    args=parser.parse_args();old,_=load_package(args.from_directory,require_current=False)
    analyzer,_=configuration(args.analysis_config,False)
    exported=export_package(args.db,args.directory,reference_time=old['reference_time'],limit=old['sampled'],
                            seed=old['seed'],state_db=args.analysis_state,analyzer=analyzer)
    csv=args.directory/'retained-human-labels.csv'
    retained=rebase_labels(args.from_directory,args.directory,args.labels_csv,csv)
    imported=import_labels(args.directory,csv,args.labels_db,labeler=args.labeler)
    report=evaluate_package(args.directory,raw_db=args.db,labels_db=args.labels_db,split='development')
    save_new(args.directory/'development.json',json.dumps(report,ensure_ascii=False,indent=2))
    summary=dict(exported=exported,retained=retained,imported=imported,human_labeled_samples=report['human_labeled_samples'],
        featured={key:value['labeled_subset_metrics'] for key,value in report['featured'].items()},
        classification=report['classification']['effective']['type'],
        undergraduate=report['undergraduate']['labeled_subset_metrics'],
        action_basis=report['action_basis']['labeled_subset_metrics'],model_requests=0)
    save_new(args.directory/'refresh.json',json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
