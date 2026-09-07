"""Non-gating cache/cost observations; unknown accounting is never free usage."""
import math

FIELDS=('cache_observed_input_tokens','cache_observed_cached_tokens','cache_usage_missing_requests',
        'priced_cost_usd','cost_usage_missing_requests')

def observe_request(data):
    out=dict.fromkeys(FIELDS,0)
    usage=data.get('usage') or {}
    meta=(data.get('output') or {}).get('metadata') or {}
    inp=usage.get('input_tokens',0);cached=usage.get('cached_tokens',0);output=usage.get('output_tokens',0)
    cache_known=meta.get('usage_reported') is True and meta.get('cache_usage_reported') is True and 0<=cached<=inp
    if cache_known:
        out['cache_observed_input_tokens']=inp;out['cache_observed_cached_tokens']=cached
    else:out['cache_usage_missing_requests']=1
    rates=[meta.get(k) for k in ('input_cost_per_million','cached_input_cost_per_million','output_cost_per_million')]
    valid_rates=all(isinstance(r,(int,float)) and not isinstance(r,bool) and math.isfinite(r) and r>=0 for r in rates)
    # All-zero defaults mean no price configured, rather than a verified free model.
    if cache_known and valid_rates and any(r>0 for r in rates):
        out['priced_cost_usd']=((inp-cached)*rates[0]+cached*rates[1]+output*rates[2])/1_000_000
    else:out['cost_usage_missing_requests']=1
    return out

def finalize(metrics):
    inp=metrics.get('cache_observed_input_tokens',0)
    metrics['cache_hit_percent']=(round(100*metrics.get('cache_observed_cached_tokens',0)/inp,4)
        if inp and not metrics.get('cache_usage_missing_requests',0) else None)
    metrics['estimated_cost_usd']=(round(metrics.get('priced_cost_usd',0),8)
        if metrics.get('model_requests',0) and not metrics.get('cost_usage_missing_requests',0) else None)
    metrics['cost_currency']='USD'
    metrics['cost_basis']='configured_rates_estimate; not a provider invoice; unreported retry cost unknown'
