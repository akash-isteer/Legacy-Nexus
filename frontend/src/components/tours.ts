import type { TourStep } from "./Tour";

export const DASHBOARD_TOUR: TourStep[] = [
  { target: "load-sample", title: "Start with the sample application", body: "Load a small COBOL / DB2 banking system (customers, accounts, interest, payments, Report 12) to see every feature in a minute." },
  { target: "new-project", title: "Or analyse your own code", body: "Create a project and upload COBOL, JCL, SQL, Java, C#, Python, JavaScript/TypeScript, shell scripts, YAML config, OpenAPI specs and notes — as files or a .zip." },
  { target: "project-list", title: "Your applications", body: "Each project shows its analysis progress. Open one when its status is Ready." },
  { target: "nav-settings", title: "Connect an AI model (optional)", body: "Parsing, graphs, rules, impact and documentation run without AI. Choose a model and add its key in Settings to get written answers in Q&A." },
  { target: "nav-guide", title: "Replay this guide any time", body: "Every page has its own short guide. Open it again from here." },
];

export const OVERVIEW_TOUR: TourStep[] = [
  { target: "stats", title: "What was found", body: "Programs analysed, critical programs, unused components, database dependencies, business rules and external interfaces." },
  { target: "app-map", title: "Application map", body: "Business capabilities derived from the code, ordered from foundation services to the processes that depend on them." },
  { target: "repo", title: "Repository details", body: "What was uploaded: files, lines, size, languages, the biggest folders and the largest files. Click a file to open it." },
  { target: "code", title: "Code details", body: "How big and how complex the programs are, and how many fall into each risk level." },
  { target: "inventory", title: "Components", body: "Everything the analysis catalogued: programs, copybooks, jobs, tables, procedures, datasets and interfaces." },
  { target: "critical", title: "Program details", body: "Every program with its size, complexity, callers and risk. Search, filter by risk level or sort, then click a row to open the code." },
  { target: "launcher", title: "One button per job to be done", body: "Ask questions, map dependencies, run impact analysis, review rules, trace lineage, generate documentation or plan the modernization." },
  { target: "sensitive", title: "Sensitive data", body: "Personal and financial fields (names, addresses, account numbers, IDs) found by name, where they are stored and which programs use them. Click a group to see each field." },
  { target: "hotspots", title: "Shared data", body: "Tables updated by more than one program are flagged. Agree who owns them before changing either program." },
];

export const ASK_TOUR: TourStep[] = [
  { target: "ask-input", title: "Ask in plain language", body: "For example: “Where is interest calculated?” Only the few most relevant code excerpts are sent to the model, never whole files." },
  { target: "ask-suggest", title: "Questions that fit this codebase", body: "Suggestions are generated from what the analysis found." },
  { target: "ask-search-mode", title: "Keyword or hybrid search", body: "With semantic search on, questions are matched by meaning as well as by words, so business language finds code that uses different names. Build the index once per project." },
  { target: "ask-trust", title: "Every answer is checked", body: "Answers return as structured JSON with file and line citations. Citations are verified against the repository and the answer abstains when evidence is missing." },
];

export const GRAPH_TOUR: TourStep[] = [
  { target: "graph-filters", title: "Choose what to see", body: "Toggle jobs, programs, tables, datasets, APIs and more. Turn on columns for field-level detail." },
  { target: "graph-search", title: "Focus on one element", body: "Search for a program or table to show only its neighbourhood." },
  { target: "graph-canvas", title: "Read it left to right", body: "Edges follow the flow: job runs program, program calls program, program writes table, table is read by the next program, which writes a report." },
];

export const BUSINESS_MAP_TOUR: TourStep[] = [
  { target: "map-search", title: "Find a file", body: "Type the name of a file, table or business area to show it and what it connects to." },
  { target: "map-filters", title: "Show only what you need", body: "Pick one business area, or hide data, outside systems and documents to make the map simpler." },
  { target: "map-canvas", title: "Click any box", body: "The panel on the right explains the file in plain words, how risky it is to change, and what else may need re-testing. Press “Follow the flow” to walk through what happens next, step by step." },
  { target: "map-switch", title: "Need more detail?", body: "Technical view shows every program, table and job separately, with filters for each type." },
];

export const IMPACT_TOUR: TourStep[] = [
  { target: "impact-input", title: "Name what is changing", body: "A column such as CUSTOMER_ADDRESS, a table, a program, a copybook or a dataset." },
  { target: "impact-run", title: "Run the analysis", body: "LegacyNexus follows data and control flow through MOVEs, SQL, CALL parameters, files and APIs and lists everything that can break, with evidence for every hop." },
];

export const RULES_TOUR: TourStep[] = [
  { target: "rules-filters", title: "Filter the rule catalogue", body: "Validation, threshold, calculation, classification and domain-value rules, each linked to its source lines." },
  { target: "rules-export", title: "Take them into migration", body: "Export to CSV, or generate parity tests from them on the Modernization page." },
];

export const MODERNIZE_TOUR: TourStep[] = [
  { target: "mod-targets", title: "Pick the target", body: "Language, database and cloud platform. The plan is recalculated from the knowledge graph." },
  { target: "mod-generate", title: "Generate the plan", body: "Readiness score, 6R strategy per program, migration waves, target architecture, DDL conversion and behavioural parity tests." },
];
