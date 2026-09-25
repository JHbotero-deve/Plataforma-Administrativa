import os
import time
import requests

TOKEN_URL='https://api.amazon.com/auth/o2/token'
API_BASE='https://creatorsapi.amazon'
_cache={'token':None,'expires':0}
RESOURCES=['images.primary.large','images.primary.medium','images.variants.large','itemInfo.title','itemInfo.byLineInfo','itemInfo.classifications','itemInfo.externalIds','itemInfo.features','itemInfo.manufactureInfo','itemInfo.productInfo','itemInfo.technicalInfo','offersV2.listings.availability','offersV2.listings.condition','offersV2.listings.dealDetails','offersV2.listings.isBuyBoxWinner','offersV2.listings.merchantInfo','offersV2.listings.price','offersV2.listings.type','browseNodeInfo.browseNodes','browseNodeInfo.websiteSalesRank','ParentASIN']

def _required(name):
    value=os.getenv(name,'').strip()
    if not value: raise RuntimeError(f'Falta la variable de entorno {name}')
    return value

def _token():
    now=time.time()
    if _cache['token'] and now < _cache['expires']: return _cache['token']
    response=requests.post(TOKEN_URL,data={'grant_type':'client_credentials','client_id':_required('AMAZON_CREDENTIAL_ID'),'client_secret':_required('AMAZON_CREDENTIAL_SECRET'),'scope':'creatorsapi::default'},timeout=20)
    if not response.ok: raise RuntimeError(f'Amazon token HTTP {response.status_code}: {response.text[:500]}')
    data=response.json(); token=data.get('access_token')
    if not token: raise RuntimeError('Amazon no devolvió access_token')
    _cache.update(token=token,expires=now+max(60,int(data.get('expires_in',3600))-60)); return token

def search_items(keyword, item_count=10):
    marketplace=os.getenv('AMAZON_MARKETPLACE','www.amazon.com').strip(); tag=_required('AMAZON_PARTNER_TAG')
    payload={'partnerTag':tag,'keywords':keyword,'marketplace':marketplace,'itemCount':max(1,min(10,item_count)),'availability':'Available','resources':RESOURCES}
    response=requests.post(f'{API_BASE}/catalog/v1/searchItems',json=payload,headers={'Authorization':f'Bearer {_token()}','Content-Type':'application/json','x-marketplace':marketplace},timeout=30)
    if not response.ok: raise RuntimeError(f'Amazon SearchItems HTTP {response.status_code}: {response.text[:800]}')
    products=[]
    for item in response.json().get('searchResult',{}).get('items',[]):
        info=item.get('itemInfo') or {}; title=((info.get('title') or {}).get('displayValue') or '').strip(); asin=item.get('asin')
        listings=(item.get('offersV2') or {}).get('listings') or []; listing=listings[0] if listings else {}; money=(listing.get('price') or {}).get('money') or {}; amount=money.get('amount')
        if not asin or not title or amount is None: continue
        images=item.get('images') or {}; primary=images.get('primary') or {}; image=primary.get('large') or primary.get('medium') or {}; merchant=(listing.get('merchantInfo') or {}).get('name'); brand=((info.get('byLineInfo') or {}).get('brand') or {}).get('displayValue')
        products.append({'external_id':asin,'title':title,'price':float(amount),'image_url':image.get('url'),'product_url':item.get('detailPageURL'),'currency':money.get('currency') or os.getenv('AMAZON_CURRENCY','USD'),'rating':None,'reviews_count':0,'sales_estimate':None,'seller':{'external_id':merchant,'name':merchant,'reputation':None},'source_metadata':{'asin':asin,'parent_asin':item.get('parentASIN'),'brand':brand,'item_info':info,'offer':listing,'browse_nodes':(item.get('browseNodeInfo') or {}).get('browseNodes'),'sales_rank':(item.get('browseNodeInfo') or {}).get('websiteSalesRank'),'variant_images':images.get('variants') or {},'marketplace':marketplace}})
    print(f'[Amazon Creators API] {keyword}: {len(products)} productos reales')
    return products
