#!/usr/bin/env python3
"""Reproduce the pinned Paradise emergency-category/address audit."""
import argparse
import csv
import json
import math
import os
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import shapely
from fastparquet import ParquetFile

HERE = Path(__file__).resolve().parent
MAIN_ID = '1cdcf1b2-9a30-4c77-b5ff-bee7d99f652e'
SAME_NAME_ID = 'eada4f82-9e93-4a1c-a9bd-381a7cef2f02'
SKYWAY_ID = 'd5a3c5d0-5a2d-481f-9e4b-c7342ebd14c3'
CORE = ('06007001800','06007001900','06007002000','06007002100','06007002200')

def prepare(work, data):
    root = work
    con = duckdb.connect(str(root / 'discovery.duckdb'), config={
        'threads': 1, 'memory_limit': '512MB', 'temp_directory': str(root / 'duckdb-temp')})
    con.execute(f"""CREATE OR REPLACE TABLE raw_places AS
      SELECT id, names.primary AS name, categories.primary AS category,
        categories.alternate AS alternate, basic_category, taxonomy,
        confidence, operating_status, addresses, websites, phones, sources,
        bbox.xmin AS lon, bbox.ymin AS lat, bbox.xmax AS xmax, bbox.ymax AS ymax
      FROM read_parquet('{data / 'northern-ca-overture-pois.parquet'}')""")
    coords = con.execute('SELECT id, lon, lat, xmax, ymax FROM raw_places ORDER BY id').df()
    assert coords.id.is_unique
    assert np.allclose(coords.lon, coords.xmax) and np.allclose(coords.lat, coords.ymax)
    tracts = ParquetFile(data / 'northern-ca-census-tracts.parquet').to_pandas()
    polygons = shapely.from_wkb(tracts.geometry.to_numpy())
    assert tracts.GEOID.is_unique
    assert shapely.is_valid(polygons).all()
    points = shapely.points(coords.lon, coords.lat)
    point_ix, tract_ix = shapely.STRtree(polygons).query(points, predicate='intersects')
    assignments = pd.DataFrame({'id': coords.id.to_numpy()[point_ix],
                                'GEOID': tracts.GEOID.to_numpy()[tract_ix]})
    boundary = assignments[assignments.id.duplicated(keep=False)].sort_values(['id','GEOID'])
    boundary.to_csv(root / 'boundary-assignments.csv', index=False)
    unique_assignments = assignments.drop_duplicates('id', keep=False)
    con.execute('CREATE OR REPLACE TABLE places AS SELECT r.*, a.GEOID FROM raw_places r LEFT JOIN unique_assignments a USING (id)')
    strata = pd.read_csv(data / 'northern-ca-strata-tract-table.csv',
                        dtype={'GEOID':str, 'STATEFP':str, 'COUNTYFP':str})
    assert strata.GEOID.is_unique and set(tracts.GEOID) == set(strata.GEOID)
    con.execute('CREATE OR REPLACE TABLE strata AS SELECT * FROM strata')
    counts = dict(con.execute('SELECT GEOID, count(*) FROM places GROUP BY GEOID').fetchall())
    summary = {'places':len(coords), 'tracts':len(tracts), 'matched_places':int(assignments.id.nunique()),
               'unambiguous_places':len(unique_assignments),
               'boundary_places':int(boundary.id.nunique()),
               'unmatched_places':len(coords)-int(assignments.id.nunique()),
               'tracts_with_places':sum(k is not None for k in counts),
               'versions':{'duckdb':duckdb.__version__, 'shapely':shapely.__version__}}
    assert summary['unmatched_places'] == 0
    (root / 'join-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    con.close()


def audit(work):
    ROOT = work
    con = duckdb.connect(str(ROOT / 'discovery.duckdb'), read_only=True)
    health = con.execute("""SELECT p.GEOID,id,name,category,alternate,basic_category,confidence,
        operating_status,lon,lat,
        coalesce(category='emergency_room' OR list_contains(alternate,'emergency_room'), false) AS is_emergency,
        addresses[1].freeform AS street,
        addresses[1].locality AS city, addresses[1].postcode AS postcode,
        list_filter(sources,s -> s.property='')[1].dataset AS dataset,
        list_filter(sources,s -> s.property='')[1].update_time AS root_update,
        ur_class,svi_overall,mtbs_wildfire_last_year,mtbs_wildfire_burned_pct_land
      FROM places p LEFT JOIN strata s USING(GEOID)
      WHERE list_contains(taxonomy.hierarchy,'health_care')""").df()

    def normalized(value):
        return re.sub(r'[^a-z0-9]', '', str(value).casefold()) if pd.notna(value) else ''

    def span_km(group):
        xy = np.radians(group[['lon','lat']].to_numpy(dtype=float))
        dx = xy[:,None,0] - xy[None,:,0]
        dy = xy[:,None,1] - xy[None,:,1]
        a = np.sin(dy/2)**2 + np.cos(xy[:,None,1])*np.cos(xy[None,:,1])*np.sin(dx/2)**2
        return float(np.max(2*6371.0088*np.arcsin(np.sqrt(np.clip(a,0,1)))))

    health['key'] = (health.street.map(normalized) + '|' + health.city.map(normalized)
                     + '|' + health.postcode.fillna('').str[:5])
    health = health[health.street.notna() & health.city.notna()].copy()
    rows=[]
    for key,g in health.groupby('key',sort=True):
        if len(g)<2:
            continue
        # Modal tract identifies the address cluster; ties use the first sorted GEOID.
        modal = g.GEOID.dropna().mode()
        anchor = modal.iloc[0] if len(modal) else None
        a = g[g.GEOID==anchor].iloc[0] if anchor else g.iloc[0]
        rows.append({'key':key,'records':len(g),'span_km':span_km(g),'anchor_geoid':anchor,
                     'ur_class':a.ur_class,'svi':float(a.svi_overall),
                     'modal_tie':len(modal)>1,
                     'recent_cumulative_heavy_burn':bool(a.mtbs_wildfire_last_year >= 2018 and a.mtbs_wildfire_burned_pct_land >= 50),
                     'ur_classes':';'.join(sorted(g.ur_class.dropna().unique())),
                     'svi_min':float(g.svi_overall.min()),'svi_max':float(g.svi_overall.max()),
                     'tracts':';'.join(sorted(g.GEOID.dropna().unique())),
                     'datasets':';'.join(sorted(g.dataset.dropna().unique())),
                     'names':'; '.join(g.name.fillna('')),
                     'all_open':bool(g.operating_status.eq('open').all()),
                     'has_emergency':bool(g.is_emergency.any())})
    groups=pd.DataFrame(rows).sort_values('span_km',ascending=False)
    groups.to_csv(ROOT/'health-address-groups.csv',index=False)
    cohort=groups[(groups.ur_class=='Rural') & (groups.svi>=.25) & (groups.svi<.5)]
    cohort.to_csv(ROOT/'matched-health-address-groups.csv',index=False)
    example=health[health.key=='5974pentzrd|paradise|95969']
    assert len(example)==5 and example.id.nunique()==5
    assert groups.loc[groups.key=='5974pentzrd|paradise|95969','has_emergency'].item()
    assert 5.8 < span_km(example) < 6.2
    summary={'health_records_with_address':len(health), 'address_groups_with_2plus_records':len(groups),
             'address_groups_span_over_1km':int((groups.span_km>1).sum()),
             'cohort': 'modal tract Rural, 0.25 <= SVI < 0.5',
             'cohort_groups':len(cohort),
             'cohort_span_over_1km':int((cohort.span_km>1).sum()),
             'cohort_median_span_metres':float(cohort.span_km.median()*1000),
             'pentz_5974_records':len(example),'pentz_5974_span_km':span_km(example)}
    fire = cohort[cohort.recent_cumulative_heavy_burn]
    comparison = cohort[~cohort.recent_cumulative_heavy_burn]
    a, n1, c, n2 = int((fire.span_km>1).sum()), len(fire), int((comparison.span_km>1).sum()), len(comparison)
    total_events = a+c
    def probability(k):
        return math.comb(n1,k)*math.comb(n2,total_events-k)/math.comb(n1+n2,total_events)
    observed = probability(a)
    fisher_p = sum(probability(k) for k in range(max(0,total_events-n2),min(n1,total_events)+1)
                   if probability(k) <= observed+1e-12)
    summary['exploratory_fire_comparison'] = {'fire_groups':n1,'fire_over_1km':a,
         'comparison_groups':n2,'comparison_over_1km':c,'two_sided_fisher_p':fisher_p,
         'group_label_rule':'modal GEOID; ties use first sorted GEOID'}
    assert (a,n1,c,n2)==(1,9,3,19) and abs(fisher_p-1)<1e-12
    (ROOT/'address-audit-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    con.close()


def distance_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a['lon'], a['lat'], b['lon'], b['lat']])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1, max(0, h))))


def case_results(work, data):
    con = duckdb.connect(str(work / 'discovery.duckdb'), read_only=True)
    def records(query, params=None):
        result = con.execute(query, params or [])
        columns = [c[0] for c in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    columns = 'GEOID,id,name,category,alternate,basic_category,operating_status,confidence,lon,lat,addresses,sources'
    case = records(f"""SELECT {columns} FROM places
      WHERE addresses[1].freeform='5974 Pentz Rd'
        AND addresses[1].locality='Paradise'
        AND list_contains(taxonomy.hierarchy,'health_care') ORDER BY id""")
    main = next(r for r in case if r['id']==MAIN_ID)
    same_name = next(r for r in case if r['id']==SAME_NAME_ID)
    skyway = records(f'SELECT {columns} FROM places WHERE id=?', [SKYWAY_ID])[0]
    query = f"""SELECT {columns} FROM places
      WHERE GEOID IN ({','.join(repr(g) for g in CORE)})
        AND (category='emergency_room' OR list_contains(alternate,'emergency_room'))
        AND operating_status='open' AND confidence >= 0.95 ORDER BY id"""
    candidates = records(query)
    closure = json.loads((HERE/'evidence/closure.json').read_text())
    flagged = [r['id'] for r in candidates if r['id']==closure['matched_emergency_record_id']]
    ready = [r['id'] for r in candidates if r['id'] not in flagged]
    strata = records(f"""SELECT GEOID,pop_total,ur_class,svi_overall,svi_year,cvi_overall,
      mtbs_wildfire_burned_pct_land,mtbs_wildfire_last_year FROM strata
      WHERE GEOID IN ({','.join(repr(g) for g in CORE)}) ORDER BY GEOID""")
    summary = {'snapshot':'2026-08-19.0', 'case_address':'5974 Pentz Rd, Paradise, CA 95969',
       'healthcare_records':len(case), 'all_marked_open':all(r['operating_status']=='open' for r in case),
       'geoids':sorted({r['GEOID'] for r in case}),
       'group_max_span_km':max(distance_km(a,b) for a in case for b in case),
       'same_name_pair_km':distance_km(main,same_name),
       'hospital_point_to_skyway_health_center_metres':distance_km(main,skyway)*1000,
       'emergency_candidates_before_flag':len(candidates), 'capability_conflict_review_queue':flagged,
       'emergency_candidates_after_flag':ready, 'confidence_threshold':0.95,
       'area_population_2020':sum(r['pop_total'] for r in strata),
       'closure_source':closure['url'], 'closure_license':closure['license']}
    assert len(case)==5 and summary['all_marked_open']
    assert len(candidates)==1 and flagged==[MAIN_ID] and len(ready)==0
    assert set(summary['geoids'])=={'06007002000','06007002200'}
    assert 6 < summary['group_max_span_km'] < 6.1
    assert 35 < summary['hospital_point_to_skyway_health_center_metres'] < 45
    for name, value in [('case-records',case),('skyway-record',skyway),('case-summary',summary),
                         ('emergency-query-result',candidates),('case-tracts',strata)]:
        (work/f'{name}.json').write_text(json.dumps(value,indent=2)+'\n')
    (work/'emergency-query.sql').write_text(query+';\n')
    con.close()

    # The live validator requires the platform sample's extra Eastern WA IDs.
    # All scores remain the same neutral midpoint; no reference targets are used.
    with (data/'platform-SampleSubmission.csv').open() as f:
        ids=[row['GEOID'] for row in csv.DictReader(f)]
    assert len(ids)==9794 and len(set(ids))==9794
    assert all(re.fullmatch(r'\d{11}',i) for i in ids)
    with (work/'candidate-midpoint.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['GEOID','coverage_gap_score'])
        writer.writerows((i,'0.5') for i in sorted(ids))
    summary['candidate_csv_rows']=len(ids)
    (work/'case-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    return case, main, skyway, summary


def figure(work, data, case, main, skyway, summary):
    os.environ.setdefault('MPLCONFIGDIR', str(work/'matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    fig = plt.figure(figsize=(12,6.8),facecolor='#fcfbf7')
    ax = fig.add_axes([.07,.21,.51,.59],facecolor='#f2f3ee')
    right=fig.add_axes([.64,.21,.32,.59]);right.axis('off')
    tracts=ParquetFile(data/'northern-ca-census-tracts.parquet').to_pandas(columns=['GEOID','geometry'])
    for _,row in tracts[tracts.GEOID.isin(summary['geoids'])].iterrows():
        geometry=shapely.from_wkb(row.geometry)
        for poly in (geometry.geoms if geometry.geom_type=='MultiPolygon' else [geometry]):
            ax.add_patch(Polygon(np.asarray(poly.exterior.coords),closed=True,
                         facecolor=('#e4ece7' if row.GEOID.endswith('2000') else '#f0e4d4'),
                         edgecolor='#939d94',linewidth=.8))
    old=next(r for r in case if r['id']==SAME_NAME_ID)
    ax.plot([old['lon'],main['lon']],[old['lat'],main['lat']],color='#b35a39',ls='--',lw=1.8)
    ax.scatter([r['lon'] for r in case],[r['lat'] for r in case],s=45,color='#b35a39',zorder=4)
    ax.scatter([skyway['lon']],[skyway['lat']],s=125,facecolor='none',edgecolor='#174b5c',lw=2,zorder=5)
    ax.annotate('Pentz-address cluster\n06007002000', (old['lon'],old['lat']),
                xytext=(-10,30),textcoords='offset points',ha='right',color='#513f31',fontsize=10)
    ax.annotate('Hospital + ER tag\n06007002200', (main['lon'],main['lat']),
                xytext=(9,-35),textcoords='offset points',ha='left',color='#713d2d',fontsize=10)
    ax.text(-121.611,39.754,'6.0 km',ha='center',color='#713d2d',fontsize=12,
            bbox={'facecolor':'#fcfbf7','edgecolor':'none','pad':3})
    ax.set_xlim(-121.657,-121.553);ax.set_ylim(39.735,39.779)
    ax.set_aspect(1/math.cos(math.radians(39.75)))
    ax.set_xlabel('Longitude');ax.set_ylabel('Latitude');ax.grid(alpha=.16)
    ax.ticklabel_format(useOffset=False)
    for spine in ax.spines.values():spine.set_edgecolor('#b6bcb4')
    right.text(0,1,'RECORDED DESTINATION',color='#b35a39',fontsize=10,weight='bold',va='top')
    right.text(0,.91,'Adventist Health Feather River',weight='bold',fontsize=12,va='top')
    right.text(0,.80,'Address   5974 Pentz Rd\nCategory   hospital + emergency_room\nStatus       open\nConfidence  0.9902',linespacing=1.9,va='top',fontsize=10)
    right.text(0,.40,'CLOSURE / IDENTITY CHECK',color='#174b5c',fontsize=10,weight='bold',va='top')
    right.text(0,.31,'Emergency department: Closed\nWikipedia revision 1358422327\n\nThe recorded hospital point lies\n40.8 m from the provided\n5125 Skyway health-center point.',linespacing=1.6,va='top',fontsize=10)
    fig.text(.07,.93,"Paradise's hospital identity spans two sites",fontsize=21,weight='bold',color='#23372f')
    fig.text(.07,.87,'Five healthcare records share one street address; all five carry an open status.',fontsize=11,color='#4d5b53')
    fig.text(.07,.11,'Query: emergency_room in primary / alternate category + open + confidence >= 0.95',fontsize=10,color='#4d5b53')
    fig.text(.07,.065,'Sources: Overture Maps 2026-08-19.0; supplied Census tracts; Wikipedia contributors (CC BY-SA 4.0).\nDistances are great-circle estimates from recorded points. Coordinate-fracture screening: 40 / 2,101 address groups exceed 1 km.',fontsize=8.5,color='#4d5b53',linespacing=1.6)
    fig.savefig(work/'paradise-identity.png',dpi=160,facecolor=fig.get_facecolor())
    fig.savefig(work/'paradise-identity.svg',facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,required=True,help='Directory for generated tables, database, and figure')
    p.add_argument('--data-dir',type=Path,default=HERE/'data')
    a=p.parse_args();work=a.work_dir.resolve();data=a.data_dir.resolve()
    work.mkdir(parents=True,exist_ok=True)
    prepare(work,data);audit(work)
    case, hospital, skyway, summary=case_results(work,data)
    figure(work,data,case,hospital,skyway,summary)


if __name__=='__main__':
    main()
