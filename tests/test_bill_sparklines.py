from base64 import b64decode
from datetime import date
import math
import re
from xml.etree import ElementTree

import pandas as pd
import bill_sparklines as bills


def test_monthly_series_sorts_combines_payments_preserves_zero_and_excludes_future():
    frame=pd.DataFrame({'Mese':['2026-03-01','2026-01-01','bad','2026-02-01','2026-03-10','2026-12-01'],
                        'Gas':[30,10,999,0,5,1000]})
    before=frame.copy(deep=True)
    series=bills.monthly_series(frame,'Gas',date(2026,11,8))
    assert [(str(month),value) for month,value in series]==[('2026-01',10.),('2026-02',0.),('2026-03',35.)]
    pd.testing.assert_frame_equal(frame,before)


def test_missing_months_and_amounts_are_not_invented_as_zero():
    frame=pd.DataFrame({'Mese':['2026-01-01','2026-03-01','2026-04-01','2026-05-01'],
                        'Acqua':[20,None,float('inf'),0]})
    assert [(str(month),value) for month,value in bills.monthly_series(frame,'Acqua',date(2026,11,8))]==[('2026-01',20.),('2026-05',0.)]


def test_empty_single_and_flat_zero_charts_are_safe_and_readable():
    assert 'Nessun dato' in bills.chart_html(pd.DataFrame(),'Tari','#C19A6B',date(2026,11,8))
    for values in [[0],[0,0],[27.78,27.78]]:
        frame=pd.DataFrame({'Mese':pd.date_range('2026-01-01',periods=len(values),freq='MS'),'Tari':values})
        rendered=bills.chart_html(frame,'Tari','#C19A6B',date(2026,11,8))
        encoded=re.search(r'src="data:image/svg\+xml;base64,([^"]+)"',rendered)[1]
        root=ElementTree.fromstring(b64decode(encoded))
        points=root.find('{http://www.w3.org/2000/svg}polyline').get('points')
        assert all(math.isfinite(float(v)) for point in points.split() for v in point.split(','))
        assert root.find('{http://www.w3.org/2000/svg}circle').get('fill')=='#C19A6B'
        assert 'andamento mensile' in rendered and '01/26' in b64decode(encoded).decode()
