import hawss,json,urllib.request,sys
u,t=hawss.creds()
def rq(m,path,body=None):
    req=urllib.request.Request(u+path,data=json.dumps(body).encode() if body is not None else None,method=m,headers={'Authorization':'Bearer '+t,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req))
B='/api/config/config_entries/flow'
def make(name, entity, typ, state='on', dry=False):
    f=rq('POST',B,{'handler':'history_stats'})
    f=rq('POST',f"{B}/{f['flow_id']}",{'name':name,'entity_id':entity,'type':typ})
    f=rq('POST',f"{B}/{f['flow_id']}",{'entity_id':entity,'state':[state]})
    if dry:
        print(f['type'],f.get('step_id'),[(s['name'],s.get('type')) for s in f.get('data_schema',[])]); rq('DELETE',f"{B}/{f['flow_id']}"); return
    f=rq('POST',f"{B}/{f['flow_id']}",{'start':"{{ today_at() }}",'end':"{{ now() }}",'duration':{'hours':0,'minutes':0,'seconds':0}} if False else {'start':"{{ today_at() }}",'end':"{{ now() }}"})
    return f
if __name__=='__main__':
    make('TEST','binary_sensor.annke_yard_person_occupancy','count',dry=True)
