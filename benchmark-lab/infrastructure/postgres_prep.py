import pandas as pd
import os, glob

raw_dir = os.environ.get('RAW_DATA_DIR', './out-sf0.1/graphs/csv/raw/composite-projected-fk')
pg_dir = os.environ.get('PG_CSV_DIR', './data/postgres-csv-formatted')

def load(entity, sub):
    files = glob.glob(f"{raw_dir}/{sub}/{entity}/*.csv")
    if not files: return pd.DataFrame()
    
    # Read header from our saved headers folder
    header_file = os.path.join(os.path.dirname(raw_dir), 'headers', f"{entity}-header.csv")
    if os.path.exists(header_file):
        with open(header_file, 'r') as f:
            header = f.readline().strip().split('|')
        df = pd.concat([pd.read_csv(f, sep='|', low_memory=False, names=header) for f in files])
        
        # Clean column names for processing (remove (Group) parts)
        rename_map = {}
        for h in header:
            clean = h
            if h.startswith(':ID('): clean = ':ID'
            elif h.startswith(':START_ID('): clean = ':START_ID'
            elif h.startswith(':END_ID('): clean = ':END_ID'
            rename_map[h] = clean
        df = df.rename(columns=rename_map)
        return df
    
    return pd.concat([pd.read_csv(f, sep='|', low_memory=False) for f in files])

def save(df, name, sub):
    if df is None or df.empty: return
    out_dir = os.path.join(pg_dir, sub)
    os.makedirs(out_dir, exist_ok=True)
    df.to_csv(f"{out_dir}/{name}_0_0.csv", sep='|', index=False)

print("Loading Entities...")
post = load('Post', 'dynamic')
comment = load('Comment', 'dynamic')
forum = load('Forum', 'dynamic')
org = load('Organisation', 'static')
person = load('Person', 'dynamic')
place = load('Place', 'static')
tag = load('Tag', 'static')
tagclass = load('TagClass', 'static')

print("Loading FK Edges...")
post_creator = load('Post_hasCreator_Person', 'dynamic')
forum_post = load('Forum_containerOf_Post', 'dynamic')
post_country = load('Post_isLocatedIn_Country', 'dynamic')

comment_creator = load('Comment_hasCreator_Person', 'dynamic')
comment_country = load('Comment_isLocatedIn_Country', 'dynamic')
comment_post = load('Comment_replyOf_Post', 'dynamic')
comment_comment = load('Comment_replyOf_Comment', 'dynamic')

forum_mod = load('Forum_hasModerator_Person', 'dynamic')
org_place = load('Organisation_isLocatedIn_Place', 'static')
person_city = load('Person_isLocatedIn_City', 'dynamic')
place_place = load('Place_isPartOf_Place', 'static')
tag_tagclass = load('Tag_hasType_TagClass', 'static')
tagclass_tagclass = load('TagClass_isSubclassOf_TagClass', 'static')

print("Processing Entities (Mapping foreign keys to Postgres schema)...")
if not post.empty:
    p = post.copy()
    p = p.merge(post_creator[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'creatorid'}).drop(columns=[':START_ID'])
    p = p.merge(forum_post[[':START_ID', ':END_ID']], left_on=':ID', right_on=':END_ID', how='left').rename(columns={':START_ID': 'forumid'}).drop(columns=[':END_ID'])
    p = p.merge(post_country[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'locationid'}).drop(columns=[':START_ID'])
    p = p[[':ID', 'imageFile', 'creationDate', 'locationIP', 'browserUsed', 'language', 'content', 'length', 'creatorid', 'forumid', 'locationid']]
    for col in ['creatorid', 'forumid', 'locationid']: p[col] = p[col].astype('Int64')
    save(p, 'post', 'dynamic')

if not comment.empty:
    c = comment.copy()
    c = c.merge(comment_creator[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'creatorid'}).drop(columns=[':START_ID'])
    c = c.merge(comment_country[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'locationid'}).drop(columns=[':START_ID'])
    c = c.merge(comment_post[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'replyof_post'}).drop(columns=[':START_ID'])
    c = c.merge(comment_comment[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'replyof_comment'}).drop(columns=[':START_ID'])
    c = c[[':ID', 'creationDate', 'locationIP', 'browserUsed', 'content', 'length', 'creatorid', 'locationid', 'replyof_post', 'replyof_comment']]
    for col in ['creatorid', 'locationid', 'replyof_post', 'replyof_comment']: c[col] = c[col].astype('Int64')
    save(c, 'comment', 'dynamic')

if not forum.empty:
    f = forum.copy()
    f = f.merge(forum_mod[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'moderatorid'}).drop(columns=[':START_ID'])
    f = f[[':ID', 'title', 'creationDate', 'moderatorid']]
    f['moderatorid'] = f['moderatorid'].astype('Int64')
    save(f, 'forum', 'dynamic')

if not org.empty:
    o = org.copy()
    o = o.merge(org_place[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'placeid'}).drop(columns=[':START_ID'])
    o = o[[':ID', 'type', 'name', 'url', 'placeid']]
    o['placeid'] = o['placeid'].astype('Int64')
    save(o, 'organisation', 'static')

if not person.empty:
    pe = person.copy()
    pe = pe.merge(person_city[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'placeid'}).drop(columns=[':START_ID'])
    pe = pe[[':ID', 'firstName', 'lastName', 'gender', 'birthday', 'creationDate', 'locationIP', 'browserUsed', 'placeid']]
    pe['placeid'] = pe['placeid'].astype('Int64')
    save(pe, 'person', 'dynamic')
    
    # Flatten arrays for email and language
    emails = []
    languages = []
    for _, row in person.iterrows():
        pid = row[':ID']
        if pd.notna(row['email']):
            for e in str(row['email']).split(';'):
                if e: emails.append({'personid': pid, 'email': e})
        if pd.notna(row['language']):
            for l in str(row['language']).split(';'):
                if l: languages.append({'personid': pid, 'language': l})
    if emails: save(pd.DataFrame(emails), 'person_email_emailaddress', 'dynamic')
    if languages: save(pd.DataFrame(languages), 'person_speaks_language', 'dynamic')

if not place.empty:
    pl = place.copy()
    pl = pl.merge(place_place[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'ispartof'}).drop(columns=[':START_ID'])
    pl = pl[[':ID', 'name', 'url', 'type', 'ispartof']]
    pl['ispartof'] = pl['ispartof'].astype('Int64')
    save(pl, 'place', 'static')

if not tag.empty:
    t = tag.copy()
    t = t.merge(tag_tagclass[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'tagclassid'}).drop(columns=[':START_ID'])
    t = t[[':ID', 'name', 'url', 'tagclassid']]
    t['tagclassid'] = t['tagclassid'].astype('Int64')
    save(t, 'tag', 'static')

if not tagclass.empty:
    tc = tagclass.copy()
    tc = tc.merge(tagclass_tagclass[[':START_ID', ':END_ID']], left_on=':ID', right_on=':START_ID', how='left').rename(columns={':END_ID': 'subclassof'}).drop(columns=[':START_ID'])
    tc = tc[[':ID', 'name', 'url', 'subclassof']]
    tc['subclassof'] = tc['subclassof'].astype('Int64')
    save(tc, 'tagclass', 'static')

print("Processing Edges (Reordering to match PostgreSQL COPY formats)...")
def save_edge(entity, cols, name, sub):
    df = load(entity, sub)
    if not df.empty: save(df[cols], name, sub)

save_edge('Forum_hasMember_Person', [':START_ID', ':END_ID', 'creationDate'], 'forum_hasMember_person', 'dynamic')
save_edge('Forum_hasTag_Tag', [':START_ID', ':END_ID'], 'forum_hasTag_tag', 'dynamic')
save_edge('Person_knows_Person', [':START_ID', ':END_ID', 'creationDate'], 'person_knows_person', 'dynamic')
save_edge('Person_likes_Post', [':START_ID', ':END_ID', 'creationDate'], 'person_likes_post', 'dynamic')
save_edge('Person_likes_Comment', [':START_ID', ':END_ID', 'creationDate'], 'person_likes_comment', 'dynamic')
save_edge('Person_studyAt_University', [':START_ID', ':END_ID', 'classYear'], 'person_studyAt_organisation', 'dynamic')
save_edge('Person_workAt_Company', [':START_ID', ':END_ID', 'workFrom'], 'person_workAt_organisation', 'dynamic')
save_edge('Person_hasInterest_Tag', [':START_ID', ':END_ID'], 'person_hasInterest_tag', 'dynamic')
save_edge('Post_hasTag_Tag', [':START_ID', ':END_ID'], 'post_hasTag_tag', 'dynamic')
save_edge('Comment_hasTag_Tag', [':START_ID', ':END_ID'], 'comment_hasTag_tag', 'dynamic')

print("PostgreSQL CSV formatting successfully merged and standardized!")