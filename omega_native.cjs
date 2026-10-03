#!/usr/bin/env node
'use strict';

// Compile with SwitchyOmega's own omega-pac engine, not a rule translation.
// The vendored compiler is from the official v2.5.20 XPI (GPL-3.0-or-later).
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {isIP} = require('node:net');
const {domainToASCII} = require('node:url');
const {randomUUID} = require('node:crypto');

function expand(filename) {
    return filename.startsWith('~/') ? path.join(os.homedir(), filename.slice(2)) : filename;
}

let compiler;
function loadCompiler() {
    if (!compiler) {
        const context = vm.createContext({});
        vm.runInContext(fs.readFileSync(path.join(__dirname, 'vendor', 'omega_pac.min.js'), 'utf8'),
                        context, {filename: 'omega_pac.min.js'});
        compiler = context.OmegaPac;
    }
    return compiler;
}

function proxySpec(value) {
    const url = new URL(value);
    const scheme = url.protocol.slice(0, -1);
    if (!['http', 'https', 'socks4', 'socks5'].includes(scheme) || !url.hostname ||
        url.username || url.password || url.search || url.hash || (url.pathname && url.pathname !== '/')) {
        throw new Error('Proxy must be http/https/socks4/socks5://host:port without credentials or a path');
    }
    const port = Number(url.port || ({http: 80, https: 443}[scheme]));
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new Error('Proxy port must be between 1 and 65535');
    }
    return {scheme, host: url.hostname, port};
}

function selectedProfile(options, profile) {
    if (!options || options.schemaVersion !== 2) {
        throw new Error('Expected SwitchyOmega schemaVersion 2');
    }
    const switches = Object.values(options).filter(p => p && p.profileType === 'SwitchProfile');
    profile ||= options['-startupProfileName'] || (switches.length === 1 ? switches[0].name : null);
    if (typeof profile !== 'string' || !Object.hasOwn(options, '+' + profile)) {
        throw new Error('Select an existing profile with --profile');
    }
    return profile;
}

function selectedSwitch(options, profile) {
    profile = selectedProfile(options, profile);
    const selected = options['+' + profile];
    if (!selected || selected.profileType !== 'SwitchProfile') {
        throw new Error('Website choices require a SwitchProfile');
    }
    if (selected.rules !== undefined && !Array.isArray(selected.rules)) {
        throw new Error('SwitchProfile rules must be an array');
    }
    return {profile, selected};
}

function resultProfiles(options, profile) {
    // OmegaPac attaches reference caches to profiles; keep them off the editable export.
    options = structuredClone(options);
    return Array.from(loadCompiler().Profiles.validResultProfilesFor(profile, options), p => p.name)
        .filter(name => name === 'direct' || Object.hasOwn(options, '+' + name));
}

function normalizeDomain(value) {
    if (typeof value !== 'string' || !value || /[\s/\\*?|#@%]/u.test(value)) {
        throw new Error('Domain must be a hostname or literal IP, without wildcards, paths or a port');
    }
    let domain = value.toLowerCase().replace(/\.$/, '');
    if (domain.startsWith('[') && domain.endsWith(']')) domain = domain.slice(1, -1);
    if (isIP(domain) === 6) {
        return new URL('http://[' + domain + ']/').hostname.slice(1, -1);
    }
    if (/[:\[\]]/.test(domain)) throw new Error('Invalid hostname or literal IP');
    if (isIP(domain) === 4) return domain;
    // Do not let URL/IDNA processing reinterpret nonstandard numeric IP syntax.
    if (/^[\d.]+$/.test(domain)) throw new Error('Invalid literal IPv4 address');
    domain = domainToASCII(value.toLowerCase()).toLowerCase().replace(/\.$/, '');
    if (!domain || domain.length > 253 || domain.split('.').some(label =>
        label.length > 63 || !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label))) {
        throw new Error('Invalid hostname');
    }
    return domain;
}

function scope(domain, subdomains) {
    if (typeof subdomains !== 'boolean') throw new Error('subdomains must be a boolean');
    domain = normalizeDomain(domain);
    return {domain, subdomains: subdomains && !isIP(domain)};
}

function choiceRule(choice, profiles) {
    if (!choice || typeof choice !== 'object' || Array.isArray(choice)) {
        throw new Error('Choice must contain domain, profile and subdomains');
    }
    const normalized = scope(choice.domain, choice.subdomains);
    if (typeof choice.profile !== 'string' || !profiles.includes(choice.profile)) {
        throw new Error('Choice requires an existing result profile (or direct)');
    }
    return {...normalized, profile: choice.profile};
}

function hostRules(choice) {
    const patterns = choice.subdomains ? [choice.domain, '*.' + choice.domain] : [choice.domain];
    return patterns.map(pattern => ({
        condition: {conditionType: 'HostWildcardCondition', pattern}, profileName: choice.profile,
    }));
}

function matchesScope(rule, wanted) {
    const condition = rule && rule.condition;
    if (!condition || condition.conditionType !== 'HostWildcardCondition' ||
        typeof condition.pattern !== 'string') return false;
    let pattern = condition.pattern;
    if (pattern.startsWith('*.')) {
        if (!wanted.subdomains) return false;
        pattern = pattern.slice(2);
    }
    try { return normalizeDomain(pattern) === wanted.domain; }
    catch { return false; } // Other wildcard patterns and imported rules are not hostname choices.
}

function compile(options, {profile, proxy, temporaryRules = []} = {}) {
    profile = selectedProfile(options, profile);
    if (!proxy) throw new Error('An explicit proxy URL is required');
    // Neither the explicit upstream nor temporary choices mutate the imported export.
    options = structuredClone(options);
    const fixed = options['+proxy'];
    if (!fixed || fixed.profileType !== 'FixedProfile') {
        throw new Error('Expected a FixedProfile named "proxy" in the export');
    }
    const spec = proxySpec(proxy);
    fixed.fallbackProxy = spec;
    fixed.proxyForHttp = spec;
    fixed.proxyForHttps = spec;
    fixed.proxyForFtp = spec;
    if (!Array.isArray(temporaryRules)) {
        throw new Error('temporaryRules must be an ordered array of choices');
    }
    if (temporaryRules.length) {
        const {selected} = selectedSwitch(options, profile);
        const profiles = resultProfiles(options, profile);
        const rules = temporaryRules.map(choice => choiceRule(choice, profiles));
        // Session choices are chronological; the most recent matching choice wins.
        selected.rules = rules.reverse().flatMap(hostRules).concat(selected.rules || []);
    }
    const engine = loadCompiler();
    // No "missing profile => DIRECT" fallback: errors must be visible.
    const ast = engine.PacGenerator.script(options, profile);
    const script = engine.PacGenerator.ascii(ast.print_to_string({beautify: true}));
    return {profile, script: '// SwitchyOmega native PAC; generated by omega_native.cjs\n' + script + '\n'};
}

function atomicWrite(filename, content, {createDirectory = false, preserveMode = false} = {}) {
    if (createDirectory) fs.mkdirSync(path.dirname(filename), {recursive: true});
    const mode = preserveMode ? fs.statSync(filename).mode & 0o777 : 0o600;
    const temporary = filename + '.' + randomUUID() + '.tmp';
    try {
        fs.writeFileSync(temporary, content, {mode, flag: 'wx'});
        fs.renameSync(temporary, filename);
    } finally {
        fs.rmSync(temporary, {force: true});
    }
}

function sourceFile(source) {
    if (typeof source !== 'string' || !source) throw new Error('source is required');
    return fs.realpathSync(expand(source));
}

function description(options, profile) {
    const selection = selectedSwitch(options, profile);
    return {profile: selection.profile, profiles: resultProfiles(options, selection.profile),
            rules: selection.selected.rules || []};
}

function request(args) {
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
        throw new Error('Request must be one JSON object');
    }
    if (!['compile', 'describe', 'set', 'remove'].includes(args.action)) {
        throw new Error('Unknown request action');
    }
    const source = sourceFile(args.source);
    const options = JSON.parse(fs.readFileSync(source, 'utf8'));
    if (args.action === 'compile') {
        if (typeof args.output !== 'string' || !args.output) throw new Error('output is required');
        const result = compile(options, args);
        const output = path.resolve(expand(args.output));
        if (output === source || (fs.existsSync(output) && fs.realpathSync(output) === source)) {
            throw new Error('PAC output must not replace the source export');
        }
        atomicWrite(output, result.script, {createDirectory: true});
        return {profile: result.profile, bytes: Buffer.byteLength(result.script)};
    }
    const {profile, selected} = selectedSwitch(options, args.profile);
    if (args.action === 'describe') {
        compile(options, {...args, profile, temporaryRules: []});
        return description(options, profile);
    }
    if (args.action === 'set') {
        const choice = choiceRule(args.choice, resultProfiles(options, profile));
        // Changing exact/subdomain scope must not leave an older same-host choice behind.
        selected.rules = hostRules(choice).concat((selected.rules || []).filter(rule =>
            !matchesScope(rule, {domain: choice.domain, subdomains: !isIP(choice.domain)})));
    } else {
        const wanted = scope(args.domain, args.subdomains);
        selected.rules = (selected.rules || []).filter(rule => !matchesScope(rule, wanted));
    }
    // Validate the prospective export before replacing any personal data.
    compile(options, {...args, profile, temporaryRules: []});
    const result = description(options, profile);
    atomicWrite(source, JSON.stringify(options, null, 2) + '\n', {preserveMode: true});
    return result;
}

function migrateSettings(filename) {
    filename = sourceFile(filename);
    const settings = JSON.parse(fs.readFileSync(filename, 'utf8'));
    if (!Object.hasOwn(settings, 'overrides')) return {migrated: false};
    const source = sourceFile(settings.source);
    if (source === filename) throw new Error('Settings and source must be different files');
    const options = JSON.parse(fs.readFileSync(source, 'utf8'));
    const {profile, selected} = selectedSwitch(options, settings.profile);
    const oldRules = JSON.parse(fs.readFileSync(sourceFile(settings.overrides), 'utf8'));
    if (!Array.isArray(oldRules)) throw new Error('Legacy overrides must be an ordered array');
    const profiles = resultProfiles(options, profile);
    // Old overrides were first-match ordered and always included subdomains.
    const choices = oldRules.map(rule => choiceRule(
        {...rule, subdomains: true}, profiles));
    if (choices.length) selected.rules = choices.flatMap(hostRules).concat(selected.rules || []);
    compile(options, {profile, proxy: settings.proxy});
    if (choices.length) {
        atomicWrite(source, JSON.stringify(options, null, 2) + '\n', {preserveMode: true});
    }
    delete settings.overrides;
    atomicWrite(filename, JSON.stringify(settings, null, 2) + '\n', {preserveMode: true});
    return {migrated: true, profile, choices: choices.length};
}

function main(argv) {
    if (argv.length === 1 && argv[0] === '--request') {
        console.log(JSON.stringify(request(JSON.parse(fs.readFileSync(0, 'utf8')))));
        return;
    }
    if (argv.length === 2 && argv[0] === '--migrate-settings') {
        console.log(JSON.stringify(migrateSettings(argv[1])));
        return;
    }
    const args = {};
    for (let i = 0; i < argv.length; ++i) {
        if (argv[i] === '--help') {
            console.log('Usage: node omega_native.cjs --source /path/to/OmegaOptions.bak [--profile "auto switch"]\n' +
                        '       --proxy socks5://127.0.0.1:your-port --output proxy.pac\n' +
                        '       node omega_native.cjs --request < request.json\n' +
                        '       node omega_native.cjs --migrate-settings /path/to/native.json');
            return;
        }
        const name = argv[i];
        if (!['--source', '--profile', '--proxy', '--output'].includes(name) ||
            !argv[i + 1] || argv[i + 1].startsWith('--')) {
            throw new Error('Unknown or incomplete argument: ' + name);
        }
        args[name.slice(2)] = argv[++i];
    }
    if (!args.output) throw new Error('--output is required');
    if (!args.source) throw new Error('--source is required');
    if (!args.proxy) throw new Error('--proxy is required');
    const result = request({...args, action: 'compile'});
    console.log('Native PAC: ' + result.profile + ' -> ' + path.resolve(expand(args.output)) +
                ' (' + result.bytes + ' bytes)');
}

module.exports = {compile};
if (require.main === module) {
    try { main(process.argv.slice(2)); }
    catch (error) { console.error('omega_native: ' + error.message); process.exitCode = 1; }
}
