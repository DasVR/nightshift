#!/usr/bin/env node
/**
 * Authorship audit for nightshift (report-only by default).
 *
 * Existing history contains known violations recorded in docs/AUTHORSHIP-AUDIT.md;
 * default mode always exits 0 after printing the report. Use --strict together with
 * --range=origin/main..HEAD as the pre-PR gate for new commits only.
 *
 * Node >= 18, no npm dependencies.
 */

import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const OWNER_EMAILS = new Set(
	["arriqaalraee@gmail.com", "157200408+DasVR@users.noreply.github.com"].map((e) =>
		e.toLowerCase(),
	),
);

const IGNORE_PATHS = new Set([
	"scripts/hooks/commit-msg",
	"scripts/check_authorship.mjs",
	"tests/test_authorship.py",
	"docs/AUTHORSHIP-AUDIT.md",
	"CONTRIBUTING.md",
]);

function isIgnoredPath(relPath) {
	if (IGNORE_PATHS.has(relPath)) return true;
	if (relPath.startsWith("tests/fixtures/authorship/")) return true;
	return false;
}

const MSG_TRAILER_RES = [
	{ re: /co-authored-by:/i, label: "co-authored-by trailer" },
	{ re: /generated[ -](by|with)/i, label: "generated-by attribution" },
	{ re: /generated-by:/i, label: "generated-by trailer" },
	{
		re: /made (by|with) (claude|cursor|copilot|ai)/i,
		label: "made-by AI attribution",
	},
	{
		re: /\b(claude|cursor|copilot|chatgpt|openai|anthropic|codex|devin|windsurf|aider|kimi|composer-2)\b/i,
		label: "tool or model name in message",
	},
	{ re: /gpt-[0-9]/i, label: "GPT model in message" },
	{ re: /\bAI\b/, label: "AI (whole word) in message" },
	{ re: /ai-(assisted|generated|written)|ai-assisted/i, label: "AI-assisted in message" },
	{ re: /vibe[- ]cod/i, label: "vibe-coding in message" },
	{ re: /🤖/, label: "robot emoji in message" },
];

const AUTHOR_NAME_RE =
	/cursor|claude|copilot|openai|anthropic|chatgpt|\bgpt\b|codex|cursoragent|\bbot\b/i;

function authorIdentityViolation(name, email) {
	const em = email.toLowerCase();
	if (AUTHOR_NAME_RE.test(name)) {
		return "author name matches AI identity pattern";
	}
	if (
		em.includes("@cursor.com") ||
		em.includes("cursoragent") ||
		em.includes("noreply@anthropic.com")
	) {
		return "author email matches AI identity pattern";
	}
	return null;
}

const FILE_VIOLATION_RES = [
	{ re: /co-authored-by:/i, label: "co-authored-by trailer" },
	{ re: /generated[ -](by|with)/i, label: "generated-by attribution" },
	{ re: /generated[- ]by:/i, label: "generated-by trailer" },
	{
		re: /generated with (claude|cursor|copilot|openai|anthropic|gpt)/i,
		label: "generated-with attribution",
	},
	{ re: /cursoragent@/i, label: "cursoragent email" },
	{ re: /noreply@anthropic\.com/i, label: "anthropic noreply email" },
	{ re: /🤖/, label: "robot emoji" },
];

const FILE_ADVISORY_RES = [
	{ re: /\bclaude\b/i, label: "mentions claude" },
	{ re: /\bcursor\b/i, label: "mentions cursor" },
	{ re: /\bcopilot\b/i, label: "mentions copilot" },
	{ re: /\bopenai\b/i, label: "mentions openai" },
	{ re: /\banthropic\b/i, label: "mentions anthropic" },
];

function git(args) {
	return execFileSync("git", args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

function parseArgs(argv) {
	const out = { strict: false, json: false, range: null, help: false };
	for (const arg of argv) {
		if (arg === "--strict") out.strict = true;
		else if (arg === "--json") out.json = true;
		else if (arg === "--help" || arg === "-h") out.help = true;
		else if (arg.startsWith("--range=")) out.range = arg.slice("--range=".length);
		else {
			console.error(`Unknown argument: ${arg}`);
			process.exit(2);
		}
	}
	return out;
}

function printHelp() {
	console.log(`Usage: node scripts/check_authorship.mjs [--strict] [--json] [--range=<rev-range>] [--help]

  Scans git history and tracked files for AI attribution and non-owner authorship.
  Default: report-only (exit 0). --strict exits 1 when violations are found.
  Pre-PR gate: node scripts/check_authorship.mjs --strict --range=origin/main..HEAD
`);
}

function scanCommitMessage(fullMessage) {
	const hits = [];
	for (const { re, label } of MSG_TRAILER_RES) {
		if (re.test(fullMessage)) {
			const line =
				fullMessage.split("\n").find((l) => re.test(l)) ?? fullMessage.slice(0, 120);
			hits.push({ label, snippet: line.trim() });
		}
	}
	return hits;
}

function scanHistory(range) {
	const logArgs = [
		"log",
		"--format=%H%x1e%h%x1e%an%x1e%ae%x1e%cn%x1e%ce%x1e%s%x1e%B%x1e---COMMIT---",
	];
	if (range) logArgs.push(range);
	else logArgs.push("--all");

	const raw = git(logArgs);
	const chunks = raw.split("---COMMIT---\n").filter((c) => c.trim());
	const violations = [];
	let commitsScanned = 0;

	for (const chunk of chunks) {
		const parts = chunk.trimEnd().split("\x1e");
		if (parts.length < 8) continue;
		const [fullHash, shortHash, an, ae, cn, ce, subject, ...bodyParts] = parts;
		const body = bodyParts.join("\x1e");
		const fullMessage = `${subject}\n${body}`.trim();
		commitsScanned++;

		for (const hit of scanCommitMessage(fullMessage)) {
			violations.push({
				kind: "trailer",
				commit: shortHash,
				fullHash,
				subject,
				author: `${an} <${ae}>`,
				line: null,
				snippet: hit.snippet,
				detail: hit.label,
			});
		}

		for (const role of [
			{ kind: "author-identity", name: an, email: ae },
			{ kind: "author-identity", name: cn, email: ce },
		]) {
			const reason = authorIdentityViolation(role.name, role.email);
			if (reason) {
				violations.push({
					kind: "author-identity",
					commit: shortHash,
					fullHash,
					subject,
					author: `${role.name} <${role.email}>`,
					line: null,
					snippet: reason,
					detail: reason,
				});
			}
		}

		if (!OWNER_EMAILS.has(ae.trim().toLowerCase())) {
			violations.push({
				kind: "non-owner-author",
				commit: shortHash,
				fullHash,
				subject,
				author: `${an} <${ae}>`,
				line: null,
				snippet: ae,
				detail: "author email not on owner allowlist",
			});
		}
	}

	return { commitsScanned, violations };
}

function scanFiles() {
	const raw = git(["ls-files", "-z"]);
	const paths = raw.split("\0").filter(Boolean);
	const violations = [];
	const advisories = [];
	let filesScanned = 0;

	for (const rel of paths) {
		if (isIgnoredPath(rel)) continue;
		let buf;
		try {
			buf = readFileSync(join(process.cwd(), rel));
		} catch {
			continue;
		}
		if (buf.includes(0)) continue;
		filesScanned++;
		const text = buf.toString("utf8");
		const lines = text.split("\n");

		for (let i = 0; i < lines.length; i++) {
			const line = lines[i];
			for (const { re, label } of FILE_VIOLATION_RES) {
				if (re.test(line)) {
					violations.push({
						kind: "file-violation",
						file: rel,
						line: i + 1,
						snippet: line.trim().slice(0, 200),
						detail: label,
					});
				}
			}
			for (const { re, label } of FILE_ADVISORY_RES) {
				if (re.test(line)) {
					advisories.push({
						kind: "advisory",
						file: rel,
						line: i + 1,
						snippet: line.trim().slice(0, 200),
						detail: label,
					});
				}
			}
		}
	}

	return { filesScanned, violations, advisories };
}

function dedupeViolations(list) {
	const seen = new Set();
	return list.filter((v) => {
		const key = v.commit
			? `${v.kind}:${v.commit}:${v.detail}:${v.snippet}`
			: `${v.kind}:${v.file}:${v.line}:${v.detail}`;
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

function main() {
	const opts = parseArgs(process.argv.slice(2));
	if (opts.help) {
		printHelp();
		process.exit(0);
	}

	const repoRoot = git(["rev-parse", "--show-toplevel"]).trim();
	process.chdir(repoRoot);

	const history = scanHistory(opts.range);
	const files = scanFiles();

	const historyViolations = dedupeViolations(history.violations);
	const fileViolations = dedupeViolations(files.violations);
	const allViolations = [...historyViolations, ...fileViolations];
	const advisories = files.advisories;

	if (opts.json) {
		const payload = {
			commitsScanned: history.commitsScanned,
			filesScanned: files.filesScanned,
			violations: allViolations.map((v) => {
				if (v.commit) {
					return {
						kind: v.kind,
						commit: v.commit,
						line: v.line,
						snippet: v.snippet,
					};
				}
				return {
					kind: v.kind,
					file: v.file,
					line: v.line,
					snippet: v.snippet,
				};
			}),
			advisories: advisories.map((a) => ({
				kind: a.kind,
				file: a.file,
				line: a.line,
				snippet: a.snippet,
			})),
		};
		console.log(JSON.stringify(payload, null, 2));
	} else {
		console.log("=== HISTORY VIOLATIONS ===");
		if (historyViolations.length === 0) {
			console.log("(none)");
		} else {
			for (const v of historyViolations) {
				console.log(
					`${v.commit} ${v.subject} — ${v.kind}: ${v.author} — ${v.snippet}`,
				);
			}
		}

		console.log("\n=== FILE VIOLATIONS ===");
		if (fileViolations.length === 0) {
			console.log("(none)");
		} else {
			for (const v of fileViolations) {
				console.log(`${v.file}:${v.line} — ${v.detail} — ${v.snippet}`);
			}
		}

		console.log("\n=== ADVISORY ===");
		if (advisories.length === 0) {
			console.log("(none)");
		} else {
			for (const a of advisories) {
				console.log(`${a.file}:${a.line} — ${a.detail} — ${a.snippet}`);
			}
		}

		console.log(
			`\nScanned ${history.commitsScanned} commits, ${files.filesScanned} files.`,
		);
		console.log(
			`Violations: ${allViolations.length}, advisories: ${advisories.length}.`,
		);
		console.log(
			allViolations.length === 0
				? "Verdict: CLEAN"
				: "Verdict: VIOLATIONS FOUND (report-only; use --strict to fail)",
		);
	}

	if (opts.strict && allViolations.length > 0) {
		process.exit(1);
	}
	process.exit(0);
}

main();
