"""Renders the AWS-level system architecture diagram for the Food-Access
Advisor + Watchdog project, using the `diagrams` package (mingrammer),
which draws with the official AWS architecture icon set via Graphviz.

Run: python3 system_architecture.py
Produces: system_architecture.png (in this directory)

Honesty note: as of this package's icon set, there is no dedicated glyph
yet for Bedrock AgentCore's Gateway / Memory / Identity / Observability
sub-components specifically (they're newer than the icon set). Where a
generic AWS icon stands in for one of those (API Gateway for Gateway,
IAM for Identity, CloudWatch for Observability, DynamoDB for Memory-backed
storage), the node label says so explicitly rather than implying it's the
literal service icon.
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.ml import Bedrock
from diagrams.aws.general import Users
from diagrams.aws.integration import EventbridgeScheduler
from diagrams.aws.management import Cloudwatch
from diagrams.aws.security import IAM
from diagrams.aws.network import APIGateway
from diagrams.aws.database import Dynamodb
from diagrams.aws.general import GenericDatabase, InternetAlt1

graph_attr = {
    "fontsize": "22",
    "fontname": "Helvetica",
    "bgcolor": "white",
    "pad": "0.4",
    "nodesep": "0.9",
    "ranksep": "1.3",
    "splines": "curved",
}

ADVISOR_COLOR = "#2E6DA4"   # blue — every edge that belongs to the Advisor
WATCHDOG_COLOR = "#B23A2E"  # brick red — every edge that belongs to the Watchdog
node_attr = {"fontsize": "12", "fontname": "Helvetica"}
edge_attr = {"fontsize": "11", "fontname": "Helvetica"}

with Diagram(
    "Food-Access Advisor + Watchdog — System Architecture",
    filename="/tmp/food-access-advisor/diagrams/system_architecture",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    organizer = Users("Community organizer\n/ city planner\n(on-demand question)")
    scheduler = EventbridgeScheduler("Scheduled trigger\n(e.g. monthly)")

    with Cluster("Amazon Bedrock AgentCore Runtime\n(agents built with Strands Agents SDK)"):
        with Cluster("Advisor — strands.Agent\n(on-demand, session-only)"):
            advisor_gw = APIGateway("@tool-decorated functions\n(scoped, read-only)*")

        with Cluster("Watchdog — strands.Agent\n(scheduled, persistent)"):
            watchdog_gw = APIGateway("@tool-decorated functions\n(scoped, own log only)*")

        identity = IAM("Guardrails / least-privilege\nscoping (Identity)*")
        observability = Cloudwatch("Observability\n(traces + eval)*")

    model = Bedrock("Claude via\nAmazon Bedrock\n(model calls)")

    atlas_db = GenericDatabase("USDA Food Access\nResearch Atlas\n(LRAM/SRAM, local DB)")
    osm = InternetAlt1("OpenStreetMap\nOverpass API\n(public, live)")
    flagged_log = Dynamodb("Flagged-tracts log\n(Watchdog's only\nwrite target)*")

    organizer >> Edge(label="asks a siting\nquestion", color=ADVISOR_COLOR) >> advisor_gw
    scheduler >> Edge(label="fires recheck", color=WATCHDOG_COLOR) >> watchdog_gw

    advisor_gw >> Edge(label="Advisor reads", color=ADVISOR_COLOR) >> atlas_db
    advisor_gw >> Edge(label="Advisor reads", color=ADVISOR_COLOR) >> osm
    advisor_gw >> Edge(label="Advisor: 1 LLM call\n(evidence brief)", color=ADVISOR_COLOR) >> model

    watchdog_gw >> Edge(label="Watchdog reads", color=WATCHDOG_COLOR) >> osm
    watchdog_gw >> Edge(label="Watchdog read/write\n(own rows only)", color=WATCHDOG_COLOR) >> flagged_log
    watchdog_gw >> Edge(label="Watchdog reads\n(which tracts to recheck)", color=WATCHDOG_COLOR) >> atlas_db

    [advisor_gw, watchdog_gw] >> Edge(style="dashed", color="#888888") >> observability
    identity >> Edge(style="dashed", label="scopes", color="#888888") >> [advisor_gw, watchdog_gw]
