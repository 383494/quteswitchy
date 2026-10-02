'use strict';

const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const {compile} = require('../omega_native.cjs');

function fixed(name, scheme, host, port) {
    return {name, profileType: 'FixedProfile', bypassList: [],
            fallbackProxy: {scheme, host, port}};
}
function match(kind, pattern, profile) {
    return {condition: {conditionType: kind, pattern}, profileName: profile};
}
function options() {
    return {schemaVersion: 2,
            '+proxy': fixed('proxy', 'http', 'old.invalid', 80),
            '+other': {...fixed('other', 'http', 'http.invalid', 8001),
                       proxyForHttps: {scheme: 'https', host: 'https.invalid', port: 8443}},
            '+list': {name: 'list', profileType: 'RuleListProfile', format: 'AutoProxy',
                      matchProfileName: 'proxy', defaultProfileName: 'direct',
                      ruleList: '[AutoProxy 0.2.9]\n||blocked.example\n@@||safe.blocked.example\n'},
            '+auto switch': {name: 'auto switch', profileType: 'SwitchProfile',
                             defaultProfileName: 'list', rules: [
                                 match('HostWildcardCondition', 'forced.example', 'proxy'),
                                 match('HostWildcardCondition', 'scheme.example', 'other'),
                                 match('UrlWildcardCondition', 'http://path.example/proxied*', 'proxy'),
                             ]}};
}
function resolver(input, overrides = []) {
    const result = compile(input, {profile: 'auto switch', proxy: 'socks5://127.0.0.1:1080', overrides});
    const context = vm.createContext({});
    vm.runInContext(result.script, context);
    return url => context.FindProxyForURL(url, new URL(url).hostname);
}
const socks = 'SOCKS5 127.0.0.1:1080; SOCKS 127.0.0.1:1080';

test('native upstream, rule-list exceptions, and default routing', () => {
    const decide = resolver(options());
    assert.equal(decide('https://forced.example/'), socks);
    assert.equal(decide('https://blocked.example/'), socks);
    assert.equal(decide('https://sub.blocked.example/'), socks);
    assert.equal(decide('https://safe.blocked.example/'), 'DIRECT');
    assert.equal(decide('https://unlisted.example/'), 'DIRECT');
    assert.equal(decide('https://notblocked.example/'), 'DIRECT');
});

test('same HTTP hostname can split routing by path', () => {
    const decide = resolver(options());
    assert.equal(decide('http://path.example/proxied/file'), socks);
    assert.equal(decide('http://path.example/unproxied/file'), 'DIRECT');
});

test('per-scheme proxies in other imported profiles stay intact', () => {
    const decide = resolver(options());
    assert.equal(decide('http://scheme.example/'), 'PROXY http.invalid:8001');
    assert.equal(decide('https://scheme.example/'), 'HTTPS https.invalid:8443');
});

test('local overrides take precedence and include subdomains, not lookalikes', () => {
    const decide = resolver(options(), [{domain: 'forced.example', profile: 'direct'},
                                       {domain: 'local.example', profile: 'proxy'}]);
    assert.equal(decide('http://forced.example/'), 'DIRECT');
    assert.equal(decide('https://local.example/'), socks);
    assert.equal(decide('https://sub.local.example/'), socks);
    assert.equal(decide('https://notlocal.example/'), 'DIRECT');
    assert.equal(decide('https://local.example.evil/'), 'DIRECT');
});
