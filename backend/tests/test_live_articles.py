"""Original-source fallback, freshness and preserved refusals; no network or models."""
import asyncio
import json
from datetime import UTC, datetime

from app.tools.live_articles import collect, publisher


def test_subdomains_of_one_publisher_are_not_independent_sources():
    assert publisher('https://finance.yahoo.com/a') == publisher('https://news.yahoo.com/b') == 'yahoo.com'
    assert publisher('https://news.google.com/token') == ''
    assert publisher('https://www.sec.gov/a') == ''
    assert publisher('https://markets.publisher.co.uk/a') == 'publisher.co.uk'


def test_retrieval_falls_back_across_domains_and_keeps_blocked_attempts():
    class Gateway:
        def __init__(self):
            self.calls = []
        async def execute(self, requests):
            self.calls.extend(requests)
            out = {}
            for r in requests:
                if r['tool'] != 'fetch_page':
                    items = [{'title': 'Alpha progress', 'url': f'https://{h}/story', 'fetchable': True,
                              'published': '2026-10-03T12:00:00+00:00'}
                             for h in ('blocked.example', 'one.example', 'two.example')]
                    items += [{'title': 'Alpha stale', 'url': 'https://old.example/story', 'published': '2020-01-01T00:00:00+00:00'},
                              {'title': 'Alpha headline', 'url': 'https://news.google.com/token', 'fetchable': False},
                              {'title': 'Different company', 'url': 'https://other.example/story'}]
                    out[r['id']] = {'ok': True, 'result': json.dumps({'items': items})}
                elif 'blocked' in r['args']['url']:
                    out[r['id']] = {'ok': False, 'error': 'robots refused'}
                else:
                    out[r['id']] = {'ok': True, 'result': json.dumps({'url': r['args']['url'],
                        'text': 'Alpha reported product progress. ' * 30, 'published': '', 'title': 'Alpha'})}
            return out
    gw = Gateway()
    r = asyncio.run(collect(gw, 'AAA', 'Alpha Inc.', datetime(2026, 10, 4, tzinfo=UTC)))
    assert r['domains'] == ['one.example', 'two.example']
    assert len(r['attempts']) == 3 and r['attempts'][0]['error'] == 'robots refused'
    assert all(p['text_sha256'] and p['retrieved_at'] for p in r['pages'])
    assert len([x for x in gw.calls if x['tool'] == 'fetch_page']) == 3


def test_http_success_without_article_body_does_not_pass():
    class Gateway:
        async def execute(self, requests):
            return {r['id']: {'ok': True, 'result': json.dumps(
                {'items': [{'title': 'Alpha news', 'url': 'https://one.example/story',
                            'published': '2026-10-03T00:00:00+00:00'}]} if r['tool'] != 'fetch_page'
                else {'url': 'https://one.example/story', 'text': 'Please enable cookies'})} for r in requests}
    r = asyncio.run(collect(Gateway(), 'AAA', 'Alpha', datetime(2026, 10, 4, tzinfo=UTC)))
    assert not r['pages'] and not r['domains']
    assert r['attempts'][0]['ok'] and not r['attempts'][0]['usable']


def test_comma_in_legal_name_and_tracking_urls_do_not_drop_or_repeat_articles():
    class Gateway:
        def __init__(self):
            self.fetched = []
        async def execute(self, requests):
            out = {}
            for r in requests:
                if r['tool'] != 'fetch_page':
                    items = [{'title': 'Adeia appoints CEO', 'url': f'https://one.example/news{suffix}',
                              'published': '2026-10-03T00:00:00+00:00'}
                             for suffix in ('', '?.tsrc=rss', '?utm_source=feed')]
                    items += [{'title': 'Adeia new products', 'url': 'https://two.example/news',
                               'published': '2026-10-03T00:00:00+00:00'}]
                    out[r['id']] = {'ok': True, 'result': json.dumps({'items': items})}
                else:
                    self.fetched.append(r['args']['url'])
                    out[r['id']] = {'ok': True, 'result': json.dumps({'url': r['args']['url'],
                                     'text': 'Adeia appointed a new CEO. ' * 30})}
            return out
    gw = Gateway()
    result = asyncio.run(collect(gw, 'ADEA', 'Adeia, Inc.', datetime(2026, 10, 4, tzinfo=UTC)))
    assert result['domains'] == ['one.example', 'two.example']
    assert len(gw.fetched) == 2


def test_insufficient_coverage_searches_alternative_publishers_and_keeps_refusals():
    class Gateway:
        async def execute(self, requests):
            out = {}
            for r in requests:
                if r['tool'] != 'fetch_page':
                    hosts = ('free-one.example', 'free-two.example') if r['id'].startswith('articles.fallback') else ('blocked.example',)
                    out[r['id']] = {'ok': True, 'result': json.dumps({'items': [
                        {'title': 'Alpha company progress', 'url': f'https://{h}/news',
                         'published': '2026-10-03T00:00:00+00:00'} for h in hosts]})}
                elif 'blocked' in r['args']['url']:
                    out[r['id']] = {'ok': False, 'error': 'HTTP 403'}
                else:
                    out[r['id']] = {'ok': True, 'result': json.dumps({'url': r['args']['url'],
                                     'text': 'Alpha announced product progress. ' * 30})}
            return out
    result = asyncio.run(collect(Gateway(), 'AAA', 'Alpha', datetime(2026, 10, 4, tzinfo=UTC)))
    assert result['domains'] == ['free-one.example', 'free-two.example']
    assert result['attempts'][0]['error'] == 'HTTP 403'
