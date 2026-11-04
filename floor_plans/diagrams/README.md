# Architecture diagrams

These are drawn from the Mermaid blocks in `../ARCHITECTURE_HLD_LLD.md`, which
is the source. Edit the blocks there, then re-render. Each diagram has an SVG
(scalable, for documents) and a PNG (2x, for slides).

| File | Diagram | Section |
|---|---|---|
| `01_system_context` | Who and what the platform talks to | HLD §2 |
| `02_logical_pipeline` | Ingest → understand → measure → decide → model → produce → verify | HLD §3 |
| `03_job_state_machine` | Workflow for each drawing, including review gates | HLD §4 |
| `04_deployment` | Containers, workers, storage, AI API | HLD §5 |
| `05_data_model_erd` | Database tables | LLD §9.2 |
| `06_module_dependencies` | Package modules (built, partly built, proposed) | LLD §7.1 |
| `07_run_sequence` | One run, start to finish, with human review | LLD §7.2 |
| `08_geometry_kernel` | Wall, door-cut, footprint and stepped-wall geometry | LLD §11.3 |

To re-render (Node 22, mermaid-cli 11.4.2, using the Chromium already
installed):

```bash
PUPPETEER_SKIP_DOWNLOAD=1 npm install @mermaid-js/mermaid-cli@11.4.2
echo '{"executablePath":"/opt/pw-browsers/chromium-1243/chrome-linux64/chrome","args":["--no-sandbox"]}' > pp.json
npx mmdc -p pp.json -i diagram.mmd -o diagram.svg -b white
npx mmdc -p pp.json -i diagram.mmd -o diagram.png -b white -s 2
```
