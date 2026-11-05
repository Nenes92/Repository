"""Idempotent all-day Auto events, restricted to events owned by this app."""
from datetime import timedelta
from hashlib import sha256
import json
from urllib.parse import quote

import car_history

APP = 'spese-auto-v1'
API = 'https://www.googleapis.com/calendar/v3'


def event_bodies(records, scope):
    result = {}
    for record in records:
        if record.get('kind') != 'auto':continue
        item = car_history.car_record(record)
        entries = [(str(row['id']),car_history.day(row['date']),row['amount'],True,row.get('note','')) for row in item['payments']]
        estimate = car_history.forecast(item)
        if estimate['date']:
            entries.append(('next',estimate['date'],estimate['amount'],False,''))
        for key,on,amount,paid,note in entries:
            identifier = 'spauto'+sha256(f'{scope}:{item["id"]}:{key}'.encode()).hexdigest()[:48]
            cost = car_history.euro(float(amount)) if amount is not None else 'Importo da definire'
            text = ('Pagamento registrato: ' if paid else 'Costo previsto: ')+cost
            if not paid and estimate['automatic'] and amount is not None:text += '\nStima basata sull’ultimo pagamento.'
            if note:text += '\n'+note
            result[identifier] = dict(id=identifier,summary='🚗 '+item['name']+(' · pagato' if paid else ' · da fare'),
                description=text,start={'date':on.isoformat()},end={'date':(on+timedelta(days=1)).isoformat()},
                colorId=({'auto-0':'2','auto-1':'9','auto-2':'10','auto-3':'1','auto-4':'4'}.get(item['id'],'9') if paid else '6'),
                transparency='transparent',reminders={'useDefault':not paid},
                extendedProperties={'private':{'speseApp':APP,'speseScope':scope}})
            months = int(item.get('interval_months') or 0)
            result[identifier]['recurrence'] = [f'RRULE:FREQ=MONTHLY;INTERVAL={months}'] if not paid and months else []
    return result


def fingerprint(events):
    return sha256(json.dumps(events,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def sync_events(session, calendar_id, records, scope):
    """One filtered read, then writes only for actual differences; safe to retry."""
    desired = event_bodies(records,scope)
    base = API+'/calendars/'+quote(calendar_id,safe='')+'/events'
    actual = {}
    params = [('privateExtendedProperty','speseApp='+APP),('privateExtendedProperty','speseScope='+scope),('maxResults','2500')]
    while True:
        response = session.get(base,params=params,timeout=20)
        response.raise_for_status()
        page = response.json()
        for event in page.get('items',[]):
            owned = event.get('extendedProperties',{}).get('private',{})
            if owned.get('speseApp')==APP and owned.get('speseScope')==scope:actual[event['id']]=event
        token = page.get('nextPageToken')
        if not token:break
        params = [(key,value) for key,value in params if key!='pageToken']+[('pageToken',token)]
    count = dict(created=0,updated=0,removed=0)
    for identifier,body in desired.items():
        existing = actual.get(identifier)
        if existing is None:
            response = session.post(base,json=body,params={'sendUpdates':'none'},timeout=20)
            if response.status_code==409:
                # Concurrent creation: never overwrite an event without verifying ownership.
                response = session.get(base+'/'+identifier,timeout=20)
                response.raise_for_status()
                existing = response.json()
                if existing.get('extendedProperties',{}).get('private',{}) != body['extendedProperties']['private']:
                    raise ValueError('Conflitto con un evento non gestito dall’app.')
            else:
                response.raise_for_status()
                count['created']+=1
                continue
        changed = {key:value for key,value in body.items() if key!='id' and existing.get(key,[] if key=='recurrence' else None)!=value}
        if existing.get('status')=='cancelled':changed['status']='confirmed'
        if changed:
            response = session.patch(base+'/'+identifier,json=changed,params={'sendUpdates':'none'},timeout=20)
            response.raise_for_status()
            count['updated']+=1
    for identifier in actual.keys()-desired.keys():
        response = session.delete(base+'/'+identifier,params={'sendUpdates':'none'},timeout=20)
        if response.status_code not in (404,410):response.raise_for_status()
        count['removed']+=1
    return count
