'use strict';

const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const vm = require('node:vm');
const {compile} = require('../omega_native.cjs');

const backend = path.resolve(__dirname, '../omega_native.cjs');
const upstream = 'socks5://127.0.0.1:1080';
const socks = 'SOCKS5 127.0.0.1:1080; SOCKS 127.0.0.1:1080';
const hostRule = (pattern, profileName) => ({
    condition: {conditionType: 'HostWildcardCondition', pattern}, profileName,
});

function importedOptions() {
    return {
        schemaVersion: 2,
        '-startupProfileName': 'auto switch',
        '-unrelatedSetting': {nested: ['preserve', 42]},
        '+proxy': {name: 'proxy', profileType: 'FixedProfile', color: '#123456', bypassList: [],
                   fallbackProxy: {scheme: 'http', host: 'imported.invalid', port: 3128}},
        '+other': {name: 'other', profileType: 'FixedProfile', bypassList: [],
                   fallbackProxy: {scheme: 'http', host: 'other.invalid', port: 8000}},
        '+auto switch': {name: 'auto switch', profileType: 'SwitchProfile', color: '#abcdef',
                         defaultProfileName: 'direct', rules: [hostRule('saved.example', 'proxy')]},
    };
}

function workspace(t, options = importedOptions()) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'quteswitchy-rules-'));
    t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
    const source = path.join(directory, 'OmegaOptions.bak');
    const output = path.join(directory, 'generated', 'proxy.pac');
    fs.writeFileSync(source, JSON.stringify(options, null, 2) + '\n', {mode: 0o640});
    const shared = {source, profile: 'auto switch', proxy: upstream};
    function call(action, fields = {}, success = true) {
        const child = spawnSync(process.execPath, [backend, '--request'], {
            input: JSON.stringify({...shared, action, ...fields}), encoding: 'utf8',
        });
        if (!success) {
            assert.notEqual(child.status, 0);
            assert.equal(child.stdout, '');
            assert.match(child.stderr, /omega_native: .+/);
            return child;
        }
        assert.equal(child.status, 0, child.stderr);
        assert.equal(child.stderr, '');
        return JSON.parse(child.stdout);
    }
    return {directory, source, output, shared, call,
            read: () => JSON.parse(fs.readFileSync(source, 'utf8'))};
}

function decide(script) {
    const context = vm.createContext({});
    vm.runInContext(script, context);
    return url => context.FindProxyForURL(url, new URL(url).hostname.replace(/^\[|\]$/g, ''));
}

function exportDecision(options, temporaryRules = []) {
    return decide(compile(options, {profile: 'auto switch', proxy: upstream, temporaryRules}).script);
}

test('permanent choice round-trips as ordinary Omega rules and preserves imported data', t => {
    const original = importedOptions();
    const w = workspace(t, original);
    const described = w.call('describe');
    assert.equal(described.profile, 'auto switch');
    assert.deepEqual(new Set(described.profiles), new Set(['proxy', 'other', 'direct']));
    assert.deepEqual(described.rules, original['+auto switch'].rules);
    const response = w.call('set', {choice: {domain: 'New.Example.', profile: 'proxy', subdomains: true}});
    const added = [hostRule('new.example', 'proxy'), hostRule('*.new.example', 'proxy')];
    const expected = structuredClone(original);
    expected['+auto switch'].rules.unshift(...added);
    assert.deepEqual(w.read(), expected);
    assert.deepEqual(response, {...described, rules: expected['+auto switch'].rules});
    assert.equal(fs.statSync(w.source).mode & 0o777, 0o640);
    assert.deepEqual(w.call('describe'), response);
    const route = exportDecision(w.read());
    assert.equal(route('https://new.example/'), socks);
    assert.equal(route('https://child.new.example/'), socks);
    assert.equal(route('https://notnew.example/'), 'DIRECT');
    assert.equal(route('https://new.example.evil/'), 'DIRECT');
    assert.equal(route('https://saved.example/'), socks);
});

test('setting exact scope replaces old same-host scopes without touching child rules or patterns', t => {
    const options = importedOptions();
    const untouched = [hostRule('child.site.example', 'other'), hostRule('prefix*site.example', 'other'),
        {condition: {conditionType: 'UrlWildcardCondition', pattern: 'http://site.example/special*'},
         profileName: 'other'}];
    options['+auto switch'].rules = [hostRule('Site.Example.', 'other'), hostRule('*.site.example', 'other'),
                                   ...untouched];
    const w = workspace(t, options);
    w.call('set', {choice: {domain: 'site.example', profile: 'proxy', subdomains: false}});
    assert.deepEqual(w.read()['+auto switch'].rules, [hostRule('site.example', 'proxy'), ...untouched]);
    const route = exportDecision(w.read());
    assert.equal(route('https://site.example/'), socks);
    assert.equal(route('https://unknown.site.example/'), 'DIRECT');
    assert.equal(route('https://child.site.example/'), 'PROXY other.invalid:8000');
    w.call('set', {choice: {domain: 'site.example', profile: 'direct', subdomains: true}});
    assert.deepEqual(w.read()['+auto switch'].rules,
                     [hostRule('site.example', 'direct'), hostRule('*.site.example', 'direct'), ...untouched]);
    assert.equal(exportDecision(w.read())('https://child.site.example/'), 'DIRECT');
});

test('remove honors selected scope and exact hostname boundaries', t => {
    const options = importedOptions();
    const preserved = [hostRule('child.site.example', 'other'), hostRule('*.example', 'other'),
                       hostRule('notsite.example', 'proxy')];
    options['+auto switch'].rules = [hostRule('site.example', 'proxy'), hostRule('*.site.example', 'proxy'),
                                   ...preserved];
    const w = workspace(t, options);
    w.call('remove', {domain: 'SITE.example.', subdomains: false});
    assert.deepEqual(w.read()['+auto switch'].rules, [hostRule('*.site.example', 'proxy'), ...preserved]);
    // Omega's standard *.hostname condition includes the hostname itself too.
    assert.equal(exportDecision(w.read())('https://site.example/'), socks);
    w.call('remove', {domain: 'site.example', subdomains: true});
    assert.deepEqual(w.read()['+auto switch'].rules, preserved);
    assert.equal(exportDecision(w.read())('https://child.site.example/'), 'PROXY other.invalid:8000');
    assert.equal(exportDecision(w.read())('https://notsite.example/'), 'PROXY other.invalid:8000');
});

test('IDNA and literal IP choices are normalized, with IP hosts exact-only', t => {
    const w = workspace(t);
    w.call('set', {choice: {domain: 'BÜCHER.Example.', profile: 'proxy', subdomains: true}});
    w.call('set', {choice: {domain: '192.0.2.1', profile: 'other', subdomains: true}});
    w.call('set', {choice: {domain: '[2001:0DB8:0:0::1]', profile: 'proxy', subdomains: true}});
    assert.deepEqual(w.read()['+auto switch'].rules.slice(0, 4), [
        hostRule('2001:db8::1', 'proxy'), hostRule('192.0.2.1', 'other'),
        hostRule('xn--bcher-kva.example', 'proxy'), hostRule('*.xn--bcher-kva.example', 'proxy'),
    ]);
    const route = exportDecision(w.read());
    assert.equal(route('https://bücher.example/'), socks);
    assert.equal(route('https://child.xn--bcher-kva.example/'), socks);
    assert.equal(route('https://192.0.2.1/'), 'PROXY other.invalid:8000');
    assert.equal(route('https://[2001:db8::1]/'), socks);
    assert.equal(route('https://192.0.2.1.evil/'), 'DIRECT');
    w.call('remove', {domain: 'bücher.example', subdomains: true});
    assert.equal(exportDecision(w.read())('https://xn--bcher-kva.example/'), 'DIRECT');
});

test('temporary compilation writes PAC only, newest choice wins and reset restores saved routing', t => {
    const w = workspace(t);
    const before = fs.readFileSync(w.source, 'utf8');
    const broad = {domain: 'saved.example', profile: 'other', subdomains: true};
    const exact = {domain: 'saved.example', profile: 'direct', subdomains: false};
    const response = w.call('compile', {output: w.output, temporaryRules: [broad, exact]});
    const script = fs.readFileSync(w.output, 'utf8');
    assert.deepEqual(response, {profile: 'auto switch', bytes: Buffer.byteLength(script)});
    assert.equal(decide(script)('https://saved.example/'), 'DIRECT');
    assert.equal(decide(script)('https://child.saved.example/'), 'PROXY other.invalid:8000');
    assert.equal(fs.readFileSync(w.source, 'utf8'), before);
    w.call('compile', {output: w.output, temporaryRules: [broad]});
    assert.equal(decide(fs.readFileSync(w.output, 'utf8'))('https://saved.example/'), 'PROXY other.invalid:8000');
    w.call('compile', {output: w.output, temporaryRules: []});
    assert.equal(decide(fs.readFileSync(w.output, 'utf8'))('https://saved.example/'), socks);
    assert.equal(decide(fs.readFileSync(w.output, 'utf8'))('https://child.saved.example/'), 'DIRECT');
    assert.equal(fs.readFileSync(w.source, 'utf8'), before);
});

test('invalid edits and compile validation failures leave the export byte-for-byte unchanged', t => {
    const w = workspace(t);
    const before = fs.readFileSync(w.source, 'utf8');
    for (const domain of ['*.evil.example', 'host|*', 'host/path', 'https://host', 'host:80',
                          'host?query', 'host#fragment', ' host', 'host..', '-host.example',
                          'bad_label.example', '999.1.1.1', '127.1']) {
        w.call('set', {choice: {domain, profile: 'proxy', subdomains: true}}, false);
        assert.equal(fs.readFileSync(w.source, 'utf8'), before, domain);
    }
    for (const profile of ['missing', 'auto switch', 'system']) {
        w.call('set', {choice: {domain: 'valid.example', profile, subdomains: true}}, false);
        assert.equal(fs.readFileSync(w.source, 'utf8'), before);
    }
    w.call('set', {choice: {domain: 'valid.example', profile: 'proxy', subdomains: 'yes'}}, false);
    w.call('set', {proxy: '', choice: {domain: 'valid.example', profile: 'proxy', subdomains: true}}, false);
    w.call('remove', {domain: 'valid.example', subdomains: true, proxy: 'socks5://host:notaport'}, false);
    w.call('compile', {output: w.source}, false);
    assert.equal(fs.readFileSync(w.source, 'utf8'), before);
    const broken = importedOptions();
    broken['+auto switch'].defaultProfileName = 'missing';
    const invalid = workspace(t, broken);
    const brokenBefore = fs.readFileSync(invalid.source, 'utf8');
    invalid.call('set', {choice: {domain: 'new.example', profile: 'proxy', subdomains: true}}, false);
    assert.equal(fs.readFileSync(invalid.source, 'utf8'), brokenBefore);
});

test('manual compile flags remain usable and required settings have no defaults', t => {
    const w = workspace(t);
    const child = spawnSync(process.execPath, [backend, '--source', w.source, '--profile', 'auto switch',
                                             '--proxy', upstream, '--output', w.output], {encoding: 'utf8'});
    assert.equal(child.status, 0, child.stderr);
    assert.match(child.stdout, /Native PAC: auto switch ->/);
    assert.equal(decide(fs.readFileSync(w.output, 'utf8'))('https://saved.example/'), socks);
    w.call('compile', {output: w.output, source: ''}, false);
    w.call('compile', {output: w.output, proxy: ''}, false);
    const obsolete = spawnSync(process.execPath, [backend, '--overrides', 'old.json'], {encoding: 'utf8'});
    assert.notEqual(obsolete.status, 0);
    assert.match(obsolete.stderr, /Unknown or incomplete argument: --overrides/);
});

test('migration preserves ordered legacy precedence and unrelated settings, leaving the old file unused', t => {
    const w = workspace(t);
    const overrides = path.join(w.directory, 'old-rules.json');
    const filename = path.join(w.directory, 'native.json');
    const old = [{domain: 'SAVED.example.', profile: 'direct'},
                 {domain: 'child.saved.example', profile: 'proxy'},
                 {domain: 'legacy.example', profile: 'other'}];
    fs.writeFileSync(overrides, JSON.stringify(old));
    const settings = {...w.shared, overrides, pac: w.output, unrelated: {kept: true}};
    fs.writeFileSync(filename, JSON.stringify(settings));
    const before = w.read();
    const oldBytes = fs.readFileSync(overrides, 'utf8');
    function migrate() {
        const child = spawnSync(process.execPath, [backend, '--migrate-settings', filename], {encoding: 'utf8'});
        assert.equal(child.status, 0, child.stderr);
        return JSON.parse(child.stdout);
    }
    assert.deepEqual(migrate(), {migrated: true, profile: 'auto switch', choices: 3});
    const expected = structuredClone(before);
    expected['+auto switch'].rules.unshift(hostRule('saved.example', 'direct'), hostRule('*.saved.example', 'direct'),
        hostRule('child.saved.example', 'proxy'), hostRule('*.child.saved.example', 'proxy'),
        hostRule('legacy.example', 'other'), hostRule('*.legacy.example', 'other'));
    assert.deepEqual(w.read(), expected);
    const retained = {...settings};
    delete retained.overrides;
    assert.deepEqual(JSON.parse(fs.readFileSync(filename, 'utf8')), retained);
    assert.equal(fs.readFileSync(overrides, 'utf8'), oldBytes);
    assert.equal(exportDecision(w.read())('https://child.saved.example/'), 'DIRECT');
    assert.equal(exportDecision(w.read())('https://legacy.example/'), 'PROXY other.invalid:8000');
    const migratedBytes = fs.readFileSync(w.source, 'utf8');
    assert.deepEqual(migrate(), {migrated: false});
    assert.equal(fs.readFileSync(w.source, 'utf8'), migratedBytes);
});

test('empty migration removes only the settings key; invalid migration changes neither file', t => {
    const w = workspace(t);
    const overrides = path.join(w.directory, 'old-rules.json');
    const filename = path.join(w.directory, 'native.json');
    const settings = {...w.shared, overrides, pac: w.output};
    fs.writeFileSync(filename, JSON.stringify(settings));
    fs.writeFileSync(overrides, JSON.stringify([{domain: 'invalid|*', profile: 'proxy'}]));
    const sourceBefore = fs.readFileSync(w.source, 'utf8');
    const settingsBefore = fs.readFileSync(filename, 'utf8');
    const failure = spawnSync(process.execPath, [backend, '--migrate-settings', filename], {encoding: 'utf8'});
    assert.notEqual(failure.status, 0);
    assert.equal(failure.stdout, '');
    assert.equal(fs.readFileSync(w.source, 'utf8'), sourceBefore);
    assert.equal(fs.readFileSync(filename, 'utf8'), settingsBefore);
    fs.writeFileSync(overrides, '[]');
    const success = spawnSync(process.execPath, [backend, '--migrate-settings', filename], {encoding: 'utf8'});
    assert.equal(success.status, 0, success.stderr);
    assert.equal(fs.readFileSync(w.source, 'utf8'), sourceBefore);
    const retained = {...settings};
    delete retained.overrides;
    assert.deepEqual(JSON.parse(fs.readFileSync(filename, 'utf8')), retained);
    assert.equal(fs.readFileSync(overrides, 'utf8'), '[]');
});
