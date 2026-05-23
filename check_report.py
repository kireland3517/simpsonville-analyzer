from dotenv import load_dotenv; load_dotenv()
import os
from supabase import create_client

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

row = sb.table('roi_report').select('report').eq('id','current').single().execute()
r = row.data['report']

print("=== ALL UPGRADES ===")
for u in r.get('upgrades') or []:
    roi = u.get('roi_percent', 0)
    name = u.get('name', '')
    print(f"  {roi:>5.0f}%  {name}")

print()
print("=== ALL REPAIRS ===")
for rep in r.get('repairs') or []:
    pri = rep.get('priority', '')
    name = rep.get('name', '')
    print(f"  [{pri}]  {name}")

print()
print("=== DEAL KILLERS ===")
for dk in r.get('deal_killers') or []:
    print(f"  {dk}")

print()
print("=== CHECKING PHOTO ANALYSES FOR 'DECK' ===")
rows = sb.table('photo_analyses').select('filename, analysis').execute()
deck_mentions = []
for row2 in rows.data or []:
    fn = row2.get('filename', '')
    analysis = row2.get('analysis') or {}
    text = str(analysis).lower()
    if 'deck' in text:
        issues = analysis.get('issues', [])
        upgrades = analysis.get('upgrades', [])
        deck_issues = [i for i in issues if 'deck' in i.lower()]
        deck_upgrades = [u for u in upgrades if 'deck' in u.lower()]
        if deck_issues or deck_upgrades:
            deck_mentions.append({'file': fn, 'issues': deck_issues, 'upgrades': deck_upgrades})

print(f"  Found {len(deck_mentions)} analyses mentioning deck:")
for m in deck_mentions[:10]:
    print(f"  {m['file']}")
    for i in m['issues']:
        print(f"    ISSUE: {i}")
    for u in m['upgrades']:
        print(f"    UPGRADE: {u}")
