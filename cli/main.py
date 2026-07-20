from typing import Optional
import csv
import datetime
import json
import typer
import questionary
from pathlib import Path
from functools import wraps
from rich.console import Console
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *
from cli.announcements import fetch_announcements, display_announcements
from cli.stats_handler import StatsCallbackHandler

console = Console()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    # Fixed teams that always run (not user-selectable)
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Analyst name mapping
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Sentiment Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    }

    # Report section mapping: section -> (analyst_key for filtering, finalizing_agent)
    # analyst_key: which analyst selection controls this section (None = always included)
    # finalizing_agent: which agent must be "completed" for this report to count as done
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Sentiment Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._processed_message_ids = set()

    def init_for_analysis(self, selected_analysts):
        """Initialize agent status and report sections based on selected analysts.

        Args:
            selected_analysts: List of analyst type strings (e.g., ["market", "news"])
        """
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # Build agent_status dynamically
        self.agent_status = {}

        # Add selected analysts
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # Add fixed teams
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # Build report_sections dynamically
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        # Reset other state
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._processed_message_ids.clear()

    def get_completed_reports_count(self):
        """Count reports that are finalized (their finalizing agent is completed).

        A report is considered complete when:
        1. The report section has content (not None), AND
        2. The agent responsible for finalizing that report has status "completed"

        This prevents interim updates (like debate rounds) from counting as completed.
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # Report is complete if it has content AND its finalizing agent is done
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports - use .get() to handle missing sections
        analyst_sections = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def format_tokens(n):
    """Format token count for display."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team - filter to only include agents in agent_status
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Sentiment Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Filter teams to only include agents that are in agent_status
    teams = {}
    for team, agents in all_teams.items():
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp descending (newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # Calculate how many messages we can show based on available space
    max_messages = 12

    # Get the first N messages (newest ones)
    recent_messages = all_messages[:max_messages]

    # Add messages to table (already in newest-first order)
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    # Agent progress - derived from agent_status dict
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # Report progress - based on agent completion (not just content existence)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # Build stats parts
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM and tool stats from callback handler
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token display with graceful fallback
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # Elapsed time
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", "r", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the exact ticker symbol to analyze, including exchange suffix when needed (examples: SPY, CNC.TO, 7203.T, 0700.HK)",
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 3: Output language
    console.print(
        create_question_box(
            "Step 3: Output Language",
            "Select the language for analyst reports and final decision"
        )
    )
    output_language = ask_output_language()

    # Step 4: Select analysts
    console.print(
        create_question_box(
            "Step 4: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 5: Research depth
    console.print(
        create_question_box(
            "Step 5: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # Step 6: LLM Provider
    console.print(
        create_question_box(
            "Step 6: LLM Provider", "Select your LLM provider"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()

    # Providers with regional endpoints prompt for the region as a secondary
    # step so the main dropdown stays clean (mainland China and international
    # accounts cannot share API keys).
    if selected_llm_provider == "qwen":
        selected_llm_provider, backend_url = ask_qwen_region()
    elif selected_llm_provider == "minimax":
        selected_llm_provider, backend_url = ask_minimax_region()
    elif selected_llm_provider == "glm":
        selected_llm_provider, backend_url = ask_glm_region()

    # For Ollama, surface the resolved endpoint (OLLAMA_BASE_URL vs default)
    # before model selection so it's obvious where we're connecting.
    if selected_llm_provider == "ollama":
        confirm_ollama_endpoint(backend_url)

    # Confirm the provider's API key is present; prompt the user to paste
    # one and persist it to .env if it's missing, so the analysis run
    # doesn't fail later at the first API call.
    ensure_api_key(selected_llm_provider)

    # Step 7: Thinking agents
    console.print(
        create_question_box(
            "Step 7: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 8: Provider-specific thinking configuration
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        console.print(
            create_question_box(
                "Step 8: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print(
            create_question_box(
                "Step 8: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print(
            create_question_box(
                "Step 8: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
    }


def get_ticker():
    """Get ticker symbol from user input, preserving exchange suffixes."""
    # typer.prompt strips trailing dot-suffixes on some shells (e.g. 000404.SH
    # collapses to 000404). questionary.text reads the raw line.
    ticker = questionary.text(
        "",
        validate=lambda value: (
            not value.strip()
            or (
                all(ch.isalnum() or ch in "._-^" for ch in value.strip())
                and len(value.strip()) <= 32
            )
        )
        or "Please enter a valid ticker symbol, e.g. AAPL, 000404.SZ, 0700.HK.",
    ).ask()

    if ticker is None:
        console.print("\n[red]No ticker symbol provided. Exiting...[/red]")
        raise typer.Exit(1)

    return (ticker.strip() or "SPY").upper()


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save complete analysis report to disk with organized subfolders."""
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")
    return save_path / "complete_report.md"


def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Sentiment Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(Panel(Markdown(final_state["trader_investment_plan"]), title="Trader", border_style="blue", padding=(1, 2)))

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(Panel(Markdown(risk["judge_decision"]), title="Portfolio Manager", border_style="blue", padding=(1, 2)))


def update_research_team_status(status):
    """Update status for research team members (not Trader)."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# Ordered list of analysts for status transitions
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def update_analyst_statuses(message_buffer, chunk):
    """Update analyst statuses based on accumulated report state.

    Logic:
    - Store new report content from the current chunk if present
    - Check accumulated report_sections (not just current chunk) for status
    - Analysts with reports = completed
    - First analyst without report = in_progress
    - Remaining analysts without reports = pending
    - When all analysts done, set Bull Researcher to in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # Capture new report content from current chunk
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # Determine status from accumulated sections, not just current chunk
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # When all analysts complete, transition research team to in_progress
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")

def extract_content_string(content):
    """Extract string content from various message formats.
    Returns None if no meaningful text content is found.
    """
    import ast

    def is_empty(val):
        """Check if value is empty using Python's truthiness."""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # Can't parse = real text
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


def classify_message_type(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content.

    Returns:
        (type, content) - type is one of: User, Agent, Data, Control
                        - content is extracted string or None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, 'content', None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # Fallback for unknown types
    return ("System", content)


def format_tool_args(args, max_length=80) -> str:
    """Format tool arguments for terminal display."""
    result = str(args)
    if len(result) > max_length:
        return result[:max_length - 3] + "..."
    return result

def run_analysis(checkpoint: bool = False):
    # First get all user selections
    selections = get_user_selections()

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # Provider-specific thinking configuration
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    config["checkpoint_enabled"] = checkpoint

    # Create stats callback handler for tracking LLM/tool calls
    stats_handler = StatsCallbackHandler()

    # Normalize analyst selection to predefined order (selection is a 'set', order is fixed)
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    # Initialize the graph with callbacks bound to LLMs
    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    # Initialize message buffer with selected analysts
    message_buffer.init_for_analysis(selected_analyst_keys)

    # Track start time for elapsed display
    start_time = time.time()

    # Create result directory
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper
    
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # Now start the display layout
    layout = create_layout()

    with Live(layout, refresh_per_second=4) as live:
        # Initial display
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Add initial messages
        message_buffer.add_message("System", f"Selected ticker: {selections['ticker']}")
        message_buffer.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Update agent status to in_progress for the first analyst
        first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        # Create spinner text
        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        # Initialize state and get graph args with callbacks
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"], selections["analysis_date"]
        )
        # Pass callbacks to graph config for tool execution tracking
        # (LLM tracking is handled separately via LLM constructor)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # Stream the analysis
        trace = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            # Process all messages in chunk, deduplicating by message ID
            for message in chunk.get("messages", []):
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in message_buffer._processed_message_ids:
                        continue
                    message_buffer._processed_message_ids.add(msg_id)

                msg_type, content = classify_message_type(message)
                if content and content.strip():
                    message_buffer.add_message(msg_type, content)

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(tool_call["name"], tool_call["args"])
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

            # Update analyst statuses based on report state (runs on every chunk)
            update_analyst_statuses(message_buffer, chunk)

            # Research Team - Handle Investment Debate State
            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                # Only update status when there's actual content
                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            # Trading Team
            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            # Risk Management Team - Handle Risk Debate State
            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                    )
                if judge:
                    if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                        message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                        )
                        message_buffer.update_agent_status("Aggressive Analyst", "completed")
                        message_buffer.update_agent_status("Conservative Analyst", "completed")
                        message_buffer.update_agent_status("Neutral Analyst", "completed")
                        message_buffer.update_agent_status("Portfolio Manager", "completed")

            # Update the display
            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            trace.append(chunk)

        # Streamed chunks are per-node deltas, not full state. Merge them
        # so every report field populated across the run is present.
        final_state = {}
        for chunk in trace:
            final_state.update(chunk)
        decision = graph.process_signal(final_state["final_trade_decision"])

        # Update all agent statuses to completed
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )

        # Update final report sections
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # Prompt to save report
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # Prompt to display full report
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


@app.command()
def analyze(
    checkpoint: bool = typer.Option(
        False,
        "--checkpoint",
        help="Enable checkpoint/resume: save state after each node so a crashed run can resume.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
):
    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints
        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
    run_analysis(checkpoint=checkpoint)


@app.command("crypto-scan")
def crypto_scan(
    symbols: Optional[str] = typer.Option(
        None,
        "--symbols",
        help="Comma-separated crypto symbols. Hyperliquid default examples: BTC,ETH,SOL,HYPE.",
    ),
    interval: Optional[str] = typer.Option(
        None,
        "--interval",
        help="Kline interval, e.g. 5m, 15m, 1h.",
    ),
    lookback: Optional[int] = typer.Option(
        None,
        "--lookback",
        help="Rolling lookback candles used by the scanner.",
    ),
    mode: str = typer.Option(
        "analysis",
        "--mode",
        help="Execution mode: analysis, paper, testnet, or live.",
    ),
    execute_top: bool = typer.Option(
        False,
        "--execute-top",
        help="Execute only the top risk-approved signal in the selected mode.",
    ),
    live_confirm: str = typer.Option(
        "",
        "--live-confirm",
        help="Required confirmation phrase for real live orders.",
    ),
    ai_review: bool = typer.Option(
        False,
        "--ai-review",
        help="Ask the configured LLM to review the top scanned opportunities without executing orders.",
    ),
    lana: bool = typer.Option(
        True,
        "--lana/--no-lana",
        help="Enable or disable the Lana-inspired attention/OI strategy layer.",
    ),
    hot_symbols: Optional[str] = typer.Option(
        None,
        "--hot-symbols",
        help="Comma-separated manually curated hot symbols for the Lana-inspired layer.",
    ),
    hotlist: bool = typer.Option(
        True,
        "--hotlist/--no-hotlist",
        help="Merge symbols from the local hotlist into the scan universe.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable the high-star strategy fusion layer.",
    ),
    market_quality: bool = typer.Option(
        True,
        "--market-quality/--no-market-quality",
        help="Enable or disable the Hyperliquid spread/depth/funding quality gate.",
    ),
    champion: bool = typer.Option(
        False,
        "--champion/--no-champion",
        help="Apply the current evolution champion in analysis/paper mode only.",
    ),
    champion_season: str = typer.Option(
        "auto",
        "--champion-season",
        help="Runtime season for the champion package: auto, winter, spring, summer, or autumn.",
    ),
    champion_archive: Optional[Path] = typer.Option(
        None,
        "--champion-archive",
        help="Optional evolution archive JSON path.",
    ),
):
    """Scan crypto symbols and run the personal-account risk gate."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, CryptoTradingEngine

    valid_modes = {"analysis", "paper", "testnet", "live"}
    if mode not in valid_modes:
        raise typer.BadParameter(f"mode must be one of: {', '.join(sorted(valid_modes))}")

    config = CryptoTradingConfig.from_env()
    if interval:
        config = replace(config, interval=interval)
    if lookback is not None:
        if lookback < 60:
            raise typer.BadParameter("lookback must be at least 60 for the scanner.")
        config = replace(config, lookback_limit=lookback)
    config = replace(
        config,
        execution_mode=mode,
        lana_strategy_enabled=lana,
        hotlist_enabled=hotlist,
        strategy_fusion_enabled=fusion,
        market_quality_enabled=market_quality,
    )
    if hot_symbols:
        config = replace(
            config,
            lana_hot_symbols=tuple(
                item.strip().upper() for item in hot_symbols.split(",") if item.strip()
            ),
        )
    selected_symbols = None
    if symbols:
        selected_symbols = tuple(
            item.strip().upper() for item in symbols.split(",") if item.strip()
        )
    config, champion_application = _load_runtime_champion_or_default(
        config,
        mode=mode,
        enabled=champion,
        season=champion_season,
        archive_path=champion_archive,
        symbols=selected_symbols,
    )

    engine = CryptoTradingEngine(config)
    console.print(
        Panel(
            (
                f"模式: {mode} | 交易平台: {config.exchange_provider} | "
                f"Hyperliquid Testnet: {config.hyperliquid_testnet} | 实盘开关: {config.enable_live_orders}"
            ),
            title="Crypto 个人账户扫描",
            border_style="cyan",
        )
    )
    if champion_application is not None:
        console.print(_champion_loaded_message(champion_application))

    rows = engine.scan_and_review(
        selected_symbols,
        execute_top=execute_top,
        execution_mode=mode,  # type: ignore[arg-type]
        live_confirmation=live_confirm,
    )

    table = Table(title="机会扫描与风控结果", box=box.SIMPLE_HEAVY)
    table.add_column("交易对", style="bold")
    table.add_column("方向")
    table.add_column("置信度", justify="right")
    table.add_column("入场", justify="right")
    table.add_column("止损", justify="right")
    table.add_column("止盈", justify="right")
    table.add_column("风控")
    table.add_column("数量", justify="right")
    table.add_column("执行")

    for item in rows:
        signal = item.signal
        risk = item.risk
        intent = risk.intent
        execution_message = item.execution.message if item.execution else "-"
        table.add_row(
            signal.symbol,
            signal.side,
            f"{signal.confidence:.2f}",
            f"{signal.entry_price:.4f}",
            f"{signal.stop_loss:.4f}" if signal.stop_loss else "-",
            f"{signal.take_profit:.4f}" if signal.take_profit else "-",
            "通过" if risk.approved else "拒绝",
            f"{intent.quantity:.8f}" if intent else "-",
            execution_message,
        )

    console.print(table)
    for item in rows[:3]:
        console.print(f"\n[bold]{item.signal.symbol}[/bold] {item.signal.strategy}")
        for reason in item.signal.reasons:
            console.print(f"  - {reason}")
        if item.risk.rejected_rules:
            console.print("  [red]风控拒绝原因:[/red]")
            for rule in item.risk.rejected_rules:
                console.print(f"  - {rule}")

    if ai_review:
        from tradingagents.crypto.advisor import CryptoAIAdvisor
        from tradingagents.crypto.llm_router import (
            CryptoLLMRouterNotReady,
            create_crypto_review_llm,
        )

        console.print("\n[bold cyan]AI 评审[/bold cyan]")
        try:
            llm = create_crypto_review_llm(config)
            model_name = config.ai_model or getattr(llm, "model_name", getattr(llm, "model", ""))
            review = CryptoAIAdvisor(
                llm,
                router=config.ai_router,
                model=model_name,
            ).review_structured(rows)
            console.print(Markdown(review.raw_response))
            console.print(
                f"[dim]AI router={review.router} model={review.model or '-'} "
                f"action={review.action} confidence={review.confidence:.2f}[/dim]"
            )
        except CryptoLLMRouterNotReady as exc:
            console.print(f"[yellow]{exc}[/yellow]")


@app.command("crypto-workflow")
def crypto_workflow(
    symbols: Optional[str] = typer.Option(
        None,
        "--symbols",
        help="Comma-separated crypto symbols. Hyperliquid default examples: BTC,ETH,SOL,HYPE.",
    ),
    interval: Optional[str] = typer.Option(
        None,
        "--interval",
        help="Kline interval, e.g. 5m, 15m, 1h.",
    ),
    lookback: Optional[int] = typer.Option(
        None,
        "--lookback",
        help="Rolling lookback candles used by the scanner.",
    ),
    mode: str = typer.Option(
        "analysis",
        "--mode",
        help="Execution mode: analysis, paper, testnet, or live.",
    ),
    execute_top: bool = typer.Option(
        False,
        "--execute-top",
        help="Execute only the top risk-approved signal in the selected mode.",
    ),
    live_confirm: str = typer.Option(
        "",
        "--live-confirm",
        help="Required confirmation phrase for real live orders.",
    ),
    ai_review: bool = typer.Option(
        False,
        "--ai-review",
        help="Ask the configured LLM/Hermes route to review the workflow without bypassing risk.",
    ),
    lana: bool = typer.Option(
        True,
        "--lana/--no-lana",
        help="Enable or disable the Lana-inspired attention/OI strategy layer.",
    ),
    hot_symbols: Optional[str] = typer.Option(
        None,
        "--hot-symbols",
        help="Comma-separated manually curated hot symbols for the Lana-inspired layer.",
    ),
    hotlist: bool = typer.Option(
        True,
        "--hotlist/--no-hotlist",
        help="Merge symbols from the local hotlist into the scan universe.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable the high-star strategy fusion layer.",
    ),
    save_report: bool = typer.Option(
        True,
        "--save-report/--no-save-report",
        help="Persist the workflow decision journal and per-run report files.",
    ),
    journal_dir: Optional[Path] = typer.Option(
        None,
        "--journal-dir",
        help="Directory for decision_journal.jsonl and workflow report files.",
    ),
    champion: bool = typer.Option(
        False,
        "--champion/--no-champion",
        help="Apply the current evolution champion in analysis/paper mode only.",
    ),
    champion_season: str = typer.Option(
        "auto",
        "--champion-season",
        help="Runtime season for the champion package: auto, winter, spring, summer, or autumn.",
    ),
    champion_archive: Optional[Path] = typer.Option(
        None,
        "--champion-archive",
        help="Optional evolution archive JSON path.",
    ),
):
    """Run the crypto scan as a TradingAgents-style role workflow report."""

    from dataclasses import replace

    from tradingagents.crypto import (
        CryptoTradingAgentsWorkflow,
        CryptoTradingConfig,
        write_workflow_report,
    )

    valid_modes = {"analysis", "paper", "testnet", "live"}
    if mode not in valid_modes:
        raise typer.BadParameter(f"mode must be one of: {', '.join(sorted(valid_modes))}")

    config = CryptoTradingConfig.from_env()
    if interval:
        config = replace(config, interval=interval)
    if lookback is not None:
        if lookback < 60:
            raise typer.BadParameter("lookback must be at least 60 for the scanner.")
        config = replace(config, lookback_limit=lookback)
    config = replace(
        config,
        execution_mode=mode,
        lana_strategy_enabled=lana,
        hotlist_enabled=hotlist,
        strategy_fusion_enabled=fusion,
    )
    if hot_symbols:
        config = replace(
            config,
            lana_hot_symbols=tuple(
                item.strip().upper() for item in hot_symbols.split(",") if item.strip()
            ),
        )
    selected_symbols = None
    if symbols:
        selected_symbols = tuple(
            item.strip().upper() for item in symbols.split(",") if item.strip()
        )
    config, champion_application = _load_runtime_champion_or_default(
        config,
        mode=mode,
        enabled=champion,
        season=champion_season,
        archive_path=champion_archive,
        symbols=selected_symbols,
    )

    report = CryptoTradingAgentsWorkflow(config=config).run(
        symbols=selected_symbols,
        execute_top=execute_top,
        execution_mode=mode,  # type: ignore[arg-type]
        live_confirmation=live_confirm,
        ai_review_enabled=ai_review,
    )
    console.print(
        Panel(
            f"mode={mode} | ai_review={ai_review} | execute_top={execute_top}",
            title="Crypto TradingAgents Workflow",
            border_style="green",
        )
    )
    if champion_application is not None:
        console.print(_champion_loaded_message(champion_application))
    rendered_report = report.render_markdown()
    console.print(Markdown(rendered_report))
    if save_report:
        saved = write_workflow_report(
            report,
            state_dir=journal_dir or config.state_dir,
            context={
                "command": "crypto-workflow",
                "symbols": selected_symbols or config.symbols,
                "interval": config.interval,
                "ai_review_requested": ai_review,
                "execute_top": execute_top,
                "lana_enabled": lana,
                "hotlist_enabled": hotlist,
                "strategy_fusion_enabled": fusion,
                "evolution_champion_id": (
                    champion_application.candidate_id if champion_application else None
                ),
                "evolution_champion_season": (
                    champion_application.season if champion_application else None
                ),
                "evolution_champion_season_source": (
                    champion_application.season_source if champion_application else None
                ),
                "evolution_regime_assessment": (
                    champion_application.regime_assessment if champion_application else None
                ),
            },
        )
        console.print(f"[green]Decision journal:[/green] {saved.jsonl_path}")
        console.print(f"[dim]Markdown report:[/dim] {saved.markdown_path}")
        console.print(f"[dim]JSON report:[/dim] {saved.json_path}")


@app.command("crypto-hotlist")
def crypto_hotlist(
    add: Optional[str] = typer.Option(
        None,
        "--add",
        help="Comma-separated symbols to add, e.g. SOL,HYPE,WIF.",
    ),
    source: str = typer.Option(
        "manual",
        "--source",
        help="Where this hot signal came from, e.g. x, hyperliquid, forum.",
    ),
    reason: str = typer.Option(
        "",
        "--reason",
        help="Short reason for the hot signal.",
    ),
    score: float = typer.Option(
        1.0,
        "--score",
        help="Hotness score between 0 and 1.",
    ),
    ttl_hours: float = typer.Option(
        24.0,
        "--ttl-hours",
        help="How long this signal stays active.",
    ),
    show_expired: bool = typer.Option(
        False,
        "--show-expired",
        help="Show expired entries as well.",
    ),
):
    """View or update the local hot-symbol list used by crypto scans."""

    from tradingagents.crypto import CryptoTradingConfig
    from tradingagents.crypto.hotlist import add_hot_symbol, filter_hotlist, load_hotlist

    config = CryptoTradingConfig.from_env()
    if add:
        for symbol in [item.strip().upper() for item in add.split(",") if item.strip()]:
            add_hot_symbol(
                config.hotlist_path,
                symbol=symbol,
                source=source,
                score=score,
                reason=reason,
                ttl_hours=ttl_hours,
            )
        console.print(f"[green]Hotlist updated:[/green] {config.hotlist_path}")

    entries = filter_hotlist(
        load_hotlist(config.hotlist_path),
        max_age_hours=config.hotlist_max_age_hours,
        min_score=config.hotlist_min_score,
        include_expired=show_expired,
    )
    table = Table(title=f"Crypto Hotlist: {config.hotlist_path}", box=box.SIMPLE_HEAVY)
    table.add_column("交易对", style="bold")
    table.add_column("来源")
    table.add_column("分数", justify="right")
    table.add_column("观察时间")
    table.add_column("过期时间")
    table.add_column("原因")
    for entry in entries:
        table.add_row(
            entry.symbol,
            entry.source,
            f"{entry.score:.2f}",
            entry.observed_at,
            entry.expires_at or "-",
            entry.reason or "-",
        )
    console.print(table)


@app.command("crypto-autopilot")
def crypto_autopilot(
    symbols: Optional[str] = typer.Option(
        None,
        "--symbols",
        help="Comma-separated crypto symbols. Hyperliquid default examples: BTC,ETH,SOL,HYPE.",
    ),
    interval: Optional[str] = typer.Option(
        None,
        "--interval",
        help="Kline interval, e.g. 5m, 15m, 1h.",
    ),
    lookback: Optional[int] = typer.Option(
        None,
        "--lookback",
        help="Rolling lookback candles used by the scanner.",
    ),
    interval_seconds: int = typer.Option(
        300,
        "--interval-seconds",
        help="Seconds to wait between autopilot cycles.",
    ),
    cycles: int = typer.Option(
        1,
        "--cycles",
        help="Number of cycles to run. Use 0 for an endless service loop.",
    ),
    mode: str = typer.Option(
        "analysis",
        "--mode",
        help="Execution mode: analysis, paper, testnet, or live.",
    ),
    execute_top: bool = typer.Option(
        False,
        "--execute-top",
        help="Execute only the top risk-approved signal each cycle.",
    ),
    guard_positions: bool = typer.Option(
        True,
        "--guard-positions/--no-guard-positions",
        help="Check open positions for stop/take-profit/timeout/strategy exit triggers.",
    ),
    auto_close: bool = typer.Option(
        False,
        "--auto-close/--no-auto-close",
        help="Execute reduce-only close orders when the position guardian triggers.",
    ),
    allow_live: bool = typer.Option(
        False,
        "--allow-live",
        help="Extra live-mode guard for unattended autopilot runs.",
    ),
    live_confirm: str = typer.Option(
        "",
        "--live-confirm",
        help="Required confirmation phrase for real live orders.",
    ),
    ai_review: bool = typer.Option(
        False,
        "--ai-review",
        help="Ask the configured LLM/Hermes route to review each cycle.",
    ),
    lana: bool = typer.Option(
        True,
        "--lana/--no-lana",
        help="Enable or disable the Lana-inspired attention/OI strategy layer.",
    ),
    hotlist: bool = typer.Option(
        True,
        "--hotlist/--no-hotlist",
        help="Merge symbols from the local hotlist into the scan universe.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable the high-star strategy fusion layer.",
    ),
    journal_dir: Optional[Path] = typer.Option(
        None,
        "--journal-dir",
        help="Directory for decision_journal.jsonl and workflow report files.",
    ),
    champion: bool = typer.Option(
        False,
        "--champion/--no-champion",
        help="Apply the current evolution champion in analysis/paper mode only.",
    ),
    champion_season: str = typer.Option(
        "auto",
        "--champion-season",
        help="Runtime season for the champion package: auto, winter, spring, summer, or autumn.",
    ),
    champion_archive: Optional[Path] = typer.Option(
        None,
        "--champion-archive",
        help="Optional evolution archive JSON path.",
    ),
):
    """Run repeated crypto workflow cycles for unattended automation."""

    from dataclasses import replace

    from tradingagents.crypto import (
        CryptoAutoPilot,
        CryptoAutoPilotSafetyError,
        CryptoTradingConfig,
    )

    valid_modes = {"analysis", "paper", "testnet", "live"}
    if mode not in valid_modes:
        raise typer.BadParameter(f"mode must be one of: {', '.join(sorted(valid_modes))}")
    if interval_seconds < 1:
        raise typer.BadParameter("interval-seconds must be at least 1.")
    if cycles < 0:
        raise typer.BadParameter("cycles must be 0 or a positive integer.")
    if auto_close and not guard_positions:
        raise typer.BadParameter("--auto-close requires --guard-positions.")

    config = CryptoTradingConfig.from_env()
    if interval:
        config = replace(config, interval=interval)
    if lookback is not None:
        if lookback < 60:
            raise typer.BadParameter("lookback must be at least 60 for the scanner.")
        config = replace(config, lookback_limit=lookback)
    config = replace(
        config,
        execution_mode=mode,
        lana_strategy_enabled=lana,
        hotlist_enabled=hotlist,
        strategy_fusion_enabled=fusion,
    )
    selected_symbols = None
    if symbols:
        selected_symbols = tuple(
            item.strip().upper() for item in symbols.split(",") if item.strip()
        )
    config, champion_application = _load_runtime_champion_or_default(
        config,
        mode=mode,
        enabled=champion,
        season=champion_season,
        archive_path=champion_archive,
        symbols=selected_symbols,
    )

    console.print(
        Panel(
            (
                f"mode={mode} | cycles={cycles} | interval={interval_seconds}s | "
                f"execute_top={execute_top} | auto_close={auto_close} | "
                f"ai_review={ai_review}"
            ),
            title="Crypto Autopilot",
            border_style="green",
        )
    )
    if champion_application is not None:
        console.print(_champion_loaded_message(champion_application))
    try:
        runner = CryptoAutoPilot(config)
        for result in runner.run_loop(
            symbols=selected_symbols,
            interval_seconds=interval_seconds,
            cycles=cycles,
            execution_mode=mode,  # type: ignore[arg-type]
            execute_top=execute_top,
            live_confirmation=live_confirm,
            ai_review_enabled=ai_review,
            journal_dir=journal_dir,
            allow_live=allow_live,
            guard_positions=guard_positions,
            auto_close=auto_close,
        ):
            console.print(
                f"[bold]Cycle {result.cycle}[/bold] action={result.final_action} "
                f"top={result.top_symbol} stopped={result.stopped}"
            )
            if result.position_guard is not None:
                console.print(f"[dim]{result.position_guard.summary}[/dim]")
            console.print(f"[dim]{result.execution_message}[/dim]")
            if result.saved:
                console.print(f"[green]Journal:[/green] {result.saved.jsonl_path}")
                console.print(f"[dim]Report:[/dim] {result.saved.markdown_path}")
            if result.stopped:
                console.print(f"[yellow]{result.reason}[/yellow]")
    except CryptoAutoPilotSafetyError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("crypto-attention-ingest")
def crypto_attention_ingest(
    text: Optional[str] = typer.Option(
        None,
        "--text",
        help="Raw social/forum text to parse for hot crypto symbols.",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        help="UTF-8 text file containing posts/messages to parse.",
    ),
    source: str = typer.Option(
        "manual-text",
        "--source",
        help="Source label, e.g. x, hyperliquid, forum, telegram.",
    ),
    min_mentions: int = typer.Option(
        1,
        "--min-mentions",
        help="Minimum mentions required before a symbol enters the hotlist.",
    ),
    ttl_hours: float = typer.Option(
        24.0,
        "--ttl-hours",
        help="How long extracted symbols stay active.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show extracted candidates without writing hotlist.",
    ),
):
    """Parse attention text and write discovered symbols into the hotlist."""

    from tradingagents.crypto import CryptoTradingConfig
    from tradingagents.crypto.attention import (
        candidates_to_hot_symbols,
        extract_attention_candidates,
    )
    from tradingagents.crypto.hotlist import merge_hot_symbols

    chunks: list[str] = []
    if text:
        chunks.append(text)
    if file:
        chunks.append(file.read_text(encoding="utf-8"))
    if not chunks:
        raise typer.BadParameter("Provide --text or --file.")

    config = CryptoTradingConfig.from_env()
    candidates = extract_attention_candidates(
        "\n".join(chunks),
        min_mentions=min_mentions,
    )

    table = Table(title="Attention Candidates", box=box.SIMPLE_HEAVY)
    table.add_column("交易对", style="bold")
    table.add_column("分数", justify="right")
    table.add_column("提及次数", justify="right")
    table.add_column("原因")
    for candidate in candidates:
        table.add_row(
            candidate.symbol,
            f"{candidate.score:.2f}",
            str(candidate.mentions),
            candidate.reason,
        )
    console.print(table)

    if dry_run:
        console.print("[yellow]Dry run: hotlist was not updated.[/yellow]")
        return

    entries = candidates_to_hot_symbols(candidates, source=source, ttl_hours=ttl_hours)
    merge_hot_symbols(config.hotlist_path, entries)
    console.print(f"[green]Hotlist updated:[/green] {config.hotlist_path}")


@app.command("crypto-attention-harvest")
def crypto_attention_harvest(
    source_dir: Optional[Path] = typer.Option(
        None,
        "--source-dir",
        help="Directory of UTF-8 .txt files collected from X, Hyperliquid ecosystem posts, forums, or news.",
    ),
    source: str = typer.Option(
        "auto-harvest",
        "--source",
        help="Source label stored in the hotlist.",
    ),
    min_mentions: int = typer.Option(
        1,
        "--min-mentions",
        help="Minimum mentions before a symbol enters the hotlist.",
    ),
    ttl_hours: float = typer.Option(
        24.0,
        "--ttl-hours",
        help="How long harvested symbols stay active.",
    ),
):
    """Harvest local attention text files into the hotlist."""

    from tradingagents.crypto import AttentionHarvester, CryptoTradingConfig

    config = CryptoTradingConfig.from_env()
    result = AttentionHarvester(config).harvest(
        source_dir=source_dir,
        source=source,
        min_mentions=min_mentions,
        ttl_hours=ttl_hours,
    )
    console.print(
        Panel(
            f"files={result.files_read} | candidates={result.candidates_found}",
            title="Crypto Attention Harvest",
            border_style="cyan",
        )
    )
    console.print(f"[green]Hotlist:[/green] {result.hotlist_path}")


@app.command("crypto-positions")
def crypto_positions():
    """Show locally tracked crypto positions."""

    from tradingagents.crypto import CryptoTradingConfig, PositionStore

    config = CryptoTradingConfig.from_env()
    records = PositionStore.from_state_dir(config.state_dir).load()
    table = Table(title=f"Crypto Positions: {config.state_dir}", box=box.SIMPLE_HEAVY)
    table.add_column("Symbol", style="bold")
    table.add_column("Status")
    table.add_column("Quantity", justify="right")
    table.add_column("Avg Entry", justify="right")
    table.add_column("Stop", justify="right")
    table.add_column("Take Profit", justify="right")
    table.add_column("Realized PnL", justify="right")
    for item in records.values():
        table.add_row(
            item.symbol,
            item.status,
            f"{item.quantity:.8f}",
            f"{item.avg_entry_price:.8f}",
            f"{item.stop_loss:.8f}" if item.stop_loss else "-",
            f"{item.take_profit:.8f}" if item.take_profit else "-",
            f"{item.realized_pnl_usdt:.4f}",
        )
    console.print(table)


@app.command("crypto-protective-plan")
def crypto_protective_plan():
    """Print protective sell plans for open positions."""

    from tradingagents.crypto import CryptoTradingConfig, PositionStore
    from tradingagents.crypto.protective_orders import plan_from_position

    config = CryptoTradingConfig.from_env()
    positions = PositionStore.from_state_dir(config.state_dir).active_positions()
    table = Table(title="Protective Order Plans", box=box.SIMPLE_HEAVY)
    table.add_column("Symbol", style="bold")
    table.add_column("Quantity", justify="right")
    table.add_column("Take Profit", justify="right")
    table.add_column("Stop", justify="right")
    table.add_column("Stop Limit", justify="right")
    for position in positions:
        plan = plan_from_position(position, config)
        if plan is None:
            continue
        table.add_row(
            plan.symbol,
            f"{plan.quantity:.8f}",
            f"{plan.take_profit_price:.8f}",
            f"{plan.stop_price:.8f}",
            f"{plan.stop_limit_price:.8f}",
        )
    console.print(table)


@app.command("crypto-recover-orders")
def crypto_recover_orders(
    symbols: str = typer.Option(
        ...,
        "--symbols",
        help="Comma-separated symbols to recover, e.g. BTC,ETH,SOL or BTCUSDT,ETHUSDT.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use Hyperliquid mainnet when the configured provider is hyperliquid.",
    ),
    wallet_address: Optional[str] = typer.Option(
        None,
        "--wallet-address",
        help="Optional Hyperliquid user wallet address for clearinghouse recovery.",
    ),
):
    """Recover local position state from the configured execution venue."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, OrderRecoveryService
    from tradingagents.crypto.binance_client import BinanceClient
    from tradingagents.crypto.hyperliquid_client import HyperliquidClient

    config = CryptoTradingConfig.from_env()
    provider = config.exchange_provider.strip().lower()
    if provider == "hyperliquid":
        if mainnet:
            config = replace(config, hyperliquid_testnet=False)
        if wallet_address:
            config = replace(config, hyperliquid_wallet_address=wallet_address)
    client = HyperliquidClient(config) if provider == "hyperliquid" else BinanceClient(config)
    service = OrderRecoveryService(client, config)
    table = Table(title=f"Order Recovery: {provider}", box=box.SIMPLE_HEAVY)
    table.add_column("Symbol", style="bold")
    table.add_column("Open Orders", justify="right")
    table.add_column("Trades/Positions", justify="right")
    table.add_column("Updated")
    table.add_column("Message")
    for symbol in [item.strip().upper() for item in symbols.split(",") if item.strip()]:
        result = service.recover_symbol(symbol)
        table.add_row(
            result.symbol,
            str(result.open_orders),
            str(result.trades_seen),
            "yes" if result.position_updated else "no",
            result.message,
        )
    console.print(table)


@app.command("crypto-performance")
def crypto_performance():
    """Summarize local paper/live position performance."""

    from tradingagents.crypto import CryptoTradingConfig, summarize_performance

    config = CryptoTradingConfig.from_env()
    summary = summarize_performance(config)
    table = Table(title="Crypto Performance", box=box.SIMPLE_HEAVY)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Open positions", str(summary.open_positions))
    table.add_row("Closed positions", str(summary.closed_positions))
    table.add_row("Realized PnL USDT", f"{summary.realized_pnl_usdt:.4f}")
    table.add_row("Unrealized PnL USDT", f"{summary.unrealized_pnl_usdt:.4f}")
    table.add_row("Wins", str(summary.wins))
    table.add_row("Losses", str(summary.losses))
    table.add_row("Win rate", f"{summary.win_rate:.2%}")
    console.print(table)


@app.command("crypto-paper-status")
def crypto_paper_status():
    """Show local paper validation status and next queued command."""

    from tradingagents.crypto import CryptoTradingConfig, summarize_paper_status

    config = CryptoTradingConfig.from_env()
    summary = summarize_paper_status(config)
    table = Table(title="Crypto Paper Status", box=box.SIMPLE_HEAVY)
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Decision runs", str(summary.decision_runs))
    table.add_row("Paper orders", str(summary.paper_orders))
    table.add_row("Last action", summary.last_action)
    table.add_row("Last top symbol", summary.last_top_symbol)
    table.add_row("Last run at", summary.last_run_at)
    table.add_row("Last report", str(summary.last_report_path or "-"))
    table.add_row("Queued candidates", str(summary.queue_ready_count))
    table.add_row("Top queue command", summary.queue_top_command or "-")
    table.add_row("Top queue note", summary.queue_top_note or "-")
    console.print(table)


@app.command("crypto-hermes-check")
def crypto_hermes_check():
    """Check whether the configured Hermes model router is reachable."""

    from tradingagents.crypto import CryptoTradingConfig
    from tradingagents.crypto.llm_router import CryptoLLMRouterNotReady, HermesReviewLLM

    config = CryptoTradingConfig.from_env()
    if config.ai_router.strip().lower() != "hermes":
        console.print(
            Panel(
                (
                    f"router={config.ai_router}; set "
                    "TRADINGAGENTS_CRYPTO_AI_ROUTER=hermes to check Hermes."
                ),
                title="Hermes Router",
                border_style="yellow",
            )
        )
        return
    try:
        status = HermesReviewLLM(config).healthcheck()
    except CryptoLLMRouterNotReady as exc:
        console.print(f"[red]{exc}[/red]")
        return
    color = "green" if status.ready else "red"
    console.print(
        Panel(
            (
                f"ready={status.ready} | model={status.model} | "
                f"base={status.base_url}\n{status.message}"
            ),
            title="Hermes Router",
            border_style=color,
        )
    )


@app.command("crypto-account")
def crypto_account():
    """Show non-zero balances/account state for personal-account API verification."""

    from tradingagents.crypto import CryptoTradingConfig, CryptoTradingEngine

    config = CryptoTradingConfig.from_env()
    engine = CryptoTradingEngine(config)
    balances = engine.account_balances()

    table = Table(title=f"{config.exchange_provider} 个人账户余额", box=box.SIMPLE_HEAVY)
    table.add_column("资产", style="bold")
    table.add_column("可用", justify="right")
    table.add_column("冻结", justify="right")
    for balance in balances:
        table.add_row(balance.asset, f"{balance.free:.8f}", f"{balance.locked:.8f}")

    console.print(
        Panel(
            (
                f"Provider: {config.exchange_provider} | "
                f"Hyperliquid URL: {config.resolved_hyperliquid_base_url} | "
                f"Hyperliquid Testnet: {config.hyperliquid_testnet}"
            )
            if config.exchange_provider.strip().lower() == "hyperliquid"
            else f"Base URL: {config.resolved_base_url} | Testnet: {config.testnet}",
            title="账户连接",
            border_style="cyan",
        )
    )
    console.print(table)


@app.command("crypto-binance-check")
def crypto_binance_check(
    symbol: str = typer.Option(
        "BTCUSDT",
        "--symbol",
        help="Binance spot symbol to validate, e.g. BTCUSDT.",
    ),
    quote_order_usdt: float = typer.Option(
        11.0,
        "--quote-order-usdt",
        help="Quote notional used for the safe Binance order/test request.",
    ),
    test_order: bool = typer.Option(
        True,
        "--test-order/--no-test-order",
        help="Run Binance order/test. It validates order permission but creates no real order.",
    ),
    real_binance: bool = typer.Option(
        False,
        "--real-binance",
        help="Use real api.binance.com for this check instead of the default testnet setting.",
    ),
):
    """Run safe Binance account diagnostics for API-key integration."""

    from dataclasses import replace

    from tradingagents.crypto import BinanceDiagnostics, CryptoTradingConfig

    config = CryptoTradingConfig.from_env()
    if real_binance:
        config = replace(config, testnet=False)
    report = BinanceDiagnostics(config).run(
        symbol=symbol,
        quote_order_usdt=quote_order_usdt,
        include_order_test=test_order,
    )
    color = "green" if report.ok else "red"
    console.print(
        Panel(
            (
                f"base={report.base_url} | testnet={report.testnet} | "
                f"key={report.api_key_present} | secret={report.api_secret_present}"
            ),
            title="Binance Account Diagnostics",
            border_style=color,
        )
    )
    table = Table(title=f"Binance Check: {report.symbol}", box=box.SIMPLE_HEAVY)
    table.add_column("Step", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    table.add_column("Details")
    for step in report.steps:
        status_style = {
            "PASS": "green",
            "WARN": "yellow",
            "FAIL": "red",
            "SKIP": "dim",
        }.get(step.status, "white")
        detail = ", ".join(f"{key}={value}" for key, value in step.details.items())
        table.add_row(
            step.name,
            f"[{status_style}]{step.status}[/{status_style}]",
            step.message,
            detail,
        )
    console.print(table)


@app.command("crypto-hyperliquid-check")
def crypto_hyperliquid_check(
    symbol: str = typer.Option(
        "BTC",
        "--symbol",
        help="Hyperliquid coin to validate, e.g. BTC, ETH, SOL, HYPE.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use https://api.hyperliquid.xyz instead of the default testnet URL.",
    ),
    wallet_address: Optional[str] = typer.Option(
        None,
        "--wallet-address",
        help="Optional Hyperliquid user wallet address for clearinghouseState.",
    ),
):
    """Run safe Hyperliquid trading-center diagnostics."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, HyperliquidDiagnostics

    config = CryptoTradingConfig.from_env()
    config = replace(config, exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    if wallet_address:
        config = replace(config, hyperliquid_wallet_address=wallet_address)

    report = HyperliquidDiagnostics(config).run(symbol=symbol)
    color = "green" if report.ok else "red"
    console.print(
        Panel(
            (
                f"base={report.base_url} | testnet={report.testnet} | "
                f"wallet={report.wallet_address_present} | "
                f"api_wallet={report.api_wallet_present} | "
                f"private_key={report.private_key_present}"
            ),
            title="Hyperliquid Diagnostics",
            border_style=color,
        )
    )
    table = Table(title=f"Hyperliquid Check: {report.symbol}", box=box.SIMPLE_HEAVY)
    table.add_column("Step", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    table.add_column("Details")
    for step in report.steps:
        status_style = {
            "PASS": "green",
            "WARN": "yellow",
            "FAIL": "red",
            "SKIP": "dim",
        }.get(step.status, "white")
        detail = ", ".join(f"{key}={value}" for key, value in step.details.items())
        table.add_row(
            step.name,
            f"[{status_style}]{step.status}[/{status_style}]",
            step.message,
            detail,
        )
    console.print(table)


@app.command("crypto-live-readiness")
def crypto_live_readiness(
    target: str = typer.Option(
        "live",
        "--target",
        help="Readiness target: paper, testnet, or live.",
    ),
    symbol: str = typer.Option(
        "BTC",
        "--symbol",
        help="Hyperliquid coin used for optional network diagnostics.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Evaluate against Hyperliquid mainnet instead of the default testnet config.",
    ),
    wallet_address: Optional[str] = typer.Option(
        None,
        "--wallet-address",
        help="Optional Hyperliquid user wallet address for account diagnostics.",
    ),
    network: bool = typer.Option(
        False,
        "--network/--no-network",
        help="Run public/account Hyperliquid diagnostics in addition to local checks.",
    ),
):
    """Show read-only blockers before paper, testnet, or live execution."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, LiveReadinessChecker

    normalized_target = target.strip().lower()
    if normalized_target not in {"paper", "testnet", "live"}:
        raise typer.BadParameter("target must be one of: paper, testnet, live.")

    config = replace(CryptoTradingConfig.from_env(), exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    if wallet_address:
        config = replace(config, hyperliquid_wallet_address=wallet_address)

    report = LiveReadinessChecker(config).run(
        target=normalized_target,  # type: ignore[arg-type]
        network=network,
        symbol=symbol,
    )
    color = "green" if report.ready else "red"
    console.print(
        Panel(
            (
                f"target={report.target} | ready={report.ready} | "
                f"failures={len(report.failures)} | warnings={len(report.warnings)}"
            ),
            title="Crypto Live Readiness",
            border_style=color,
        )
    )
    table = Table(title="Readiness Checks", box=box.SIMPLE_HEAVY)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    for check in report.checks:
        status_style = {
            "PASS": "green",
            "WARN": "yellow",
            "FAIL": "red",
        }.get(check.status, "white")
        table.add_row(
            check.name,
            f"[{status_style}]{check.status}[/{status_style}]",
            check.message,
        )
    console.print(table)


@app.command("crypto-hyperliquid-markets")
def crypto_hyperliquid_markets(
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use https://api.hyperliquid.xyz instead of the default testnet URL.",
    ),
    limit: int = typer.Option(
        25,
        "--limit",
        help="Maximum markets to show.",
    ),
):
    """Show Hyperliquid market metadata."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, HyperliquidClient

    config = replace(CryptoTradingConfig.from_env(), exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    markets = HyperliquidClient(config).get_markets()
    table = Table(title=f"Hyperliquid Markets: {config.resolved_hyperliquid_base_url}")
    table.add_column("Coin", style="bold")
    table.add_column("Size Decimals", justify="right")
    table.add_column("Max Leverage", justify="right")
    table.add_column("Only Isolated")
    for market in markets[:limit]:
        table.add_row(
            market.name,
            str(market.sz_decimals),
            str(market.max_leverage),
            "yes" if market.only_isolated else "no",
        )
    console.print(table)


@app.command("crypto-market-quality")
def crypto_market_quality(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to inspect.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use https://api.hyperliquid.xyz instead of the default testnet URL.",
    ),
    max_spread_bps: Optional[float] = typer.Option(
        None,
        "--max-spread-bps",
        help="Override max allowed bid/ask spread in basis points.",
    ),
    min_depth_usdc: Optional[float] = typer.Option(
        None,
        "--min-depth-usdc",
        help="Override minimum bid and ask depth across configured top levels.",
    ),
):
    """Inspect Hyperliquid spread, depth, imbalance, and funding gates."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, HyperliquidClient, MarketQualityGate

    config = replace(CryptoTradingConfig.from_env(), exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    if max_spread_bps is not None:
        config = replace(config, market_quality_max_spread_bps=max_spread_bps)
    if min_depth_usdc is not None:
        config = replace(config, market_quality_min_depth_usdc=min_depth_usdc)

    client = HyperliquidClient(config)
    gate = MarketQualityGate(config, client)
    selected = tuple(item.strip().upper() for item in symbols.split(",") if item.strip())

    table = Table(
        title=f"Hyperliquid Market Quality: {config.resolved_hyperliquid_base_url}",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("Coin", style="bold")
    table.add_column("Pass")
    table.add_column("Score", justify="right")
    table.add_column("Spread bps", justify="right")
    table.add_column("Bid Depth", justify="right")
    table.add_column("Ask Depth", justify="right")
    table.add_column("Imbalance", justify="right")
    table.add_column("Funding", justify="right")
    table.add_column("Open Interest", justify="right")
    table.add_column("Reason")

    for symbol in selected:
        decision = gate.evaluate(symbol)
        pass_style = "green" if decision.approved else "red"
        table.add_row(
            decision.symbol,
            f"[{pass_style}]{'yes' if decision.approved else 'no'}[/{pass_style}]",
            f"{decision.score:.2f}",
            f"{decision.spread_bps:.2f}" if decision.spread_bps is not None else "-",
            f"{decision.bid_depth_usdc:.0f}",
            f"{decision.ask_depth_usdc:.0f}",
            f"{decision.imbalance:+.2f}" if decision.imbalance is not None else "-",
            f"{decision.funding_rate:+.5f}" if decision.funding_rate is not None else "-",
            f"{decision.open_interest:.0f}" if decision.open_interest is not None else "-",
            "; ".join(decision.reasons),
        )
    console.print(table)


@app.command("crypto-hyperliquid-stream")
def crypto_hyperliquid_stream(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to stream.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use https://api.hyperliquid.xyz instead of the default testnet URL.",
    ),
    seconds: int = typer.Option(
        60,
        "--seconds",
        help="Seconds to run. Use 0 for an uninterrupted service loop.",
    ),
    interval: Optional[str] = typer.Option(
        None,
        "--interval",
        help="Candle interval subscription. Defaults to config interval.",
    ),
    user_events: bool = typer.Option(
        False,
        "--user-events/--no-user-events",
        help="Also subscribe to wallet userEvents, fills, order updates, and funding events.",
    ),
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional JSONL path. Defaults to TRADINGAGENTS_CRYPTO_STATE_DIR/events.",
    ),
):
    """Archive Hyperliquid WebSocket events for real-time analysis."""

    from dataclasses import replace

    from tradingagents.crypto import (
        CryptoTradingConfig,
        HyperliquidEventArchive,
        HyperliquidStreamError,
        HyperliquidStreamService,
    )

    selected = tuple(item.strip().upper() for item in symbols.split(",") if item.strip())
    config = replace(CryptoTradingConfig.from_env(), exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    archive = HyperliquidEventArchive(archive_path) if archive_path else None
    service = HyperliquidStreamService(
        config,
        symbols=selected,
        interval=interval,
        archive=archive,
    )
    try:
        planned = service.subscription_plan(user_events=user_events)
    except HyperliquidStreamError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        Panel(
            (
                f"base={config.resolved_hyperliquid_base_url} | "
                f"symbols={','.join(selected)} | interval={interval or config.interval} | "
                f"subscriptions={len(planned)} | user_events={user_events} | seconds={seconds}"
            ),
            title="Hyperliquid WebSocket Stream",
            border_style="cyan",
        )
    )
    try:
        summary = service.run(duration_seconds=seconds, user_events=user_events)
    except HyperliquidStreamError as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(
        Panel(
            (
                f"subscriptions={summary.subscriptions} | events={summary.events} | "
                f"archive={summary.archive_path}"
            ),
            title="Stream Archive Summary",
            border_style="green",
        )
    )


@app.command("crypto-regime")
def crypto_regime(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins used by the regime classifier.",
    ),
    mainnet: bool = typer.Option(
        True,
        "--mainnet/--testnet",
        help="Use mainnet public candles or testnet public candles.",
    ),
    interval: str = typer.Option(
        "1h",
        "--interval",
        help="Kline interval for regime water-level classification.",
    ),
    bars: int = typer.Option(
        96,
        "--bars",
        help="Historical candles to classify per symbol.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write a Markdown regime report.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write a JSON regime report.",
    ),
):
    """Classify the runtime season used by evolution champion packages."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, RegimeEngine

    selected = tuple(item.strip().upper() for item in symbols.split(",") if item.strip())
    if not selected:
        raise typer.BadParameter("symbols must include at least one coin.")
    if bars < 12:
        raise typer.BadParameter("bars must be at least 12.")
    config = replace(
        CryptoTradingConfig.from_env(),
        exchange_provider="hyperliquid",
        hyperliquid_testnet=not mainnet,
        interval=interval,
    )
    assessment = RegimeEngine(config).assess(selected, interval=interval, bars=bars)
    console.print(
        Panel(
            (
                f"season={assessment.season} | confidence={assessment.confidence:.2%} | "
                f"heat={assessment.heat_score:.4f} | interval={assessment.interval}"
            ),
            title="Crypto Regime Engine",
            border_style="cyan",
        )
    )
    reasons = Table(title="Regime Reasons", box=box.SIMPLE_HEAVY)
    reasons.add_column("Reason")
    for reason in assessment.reasons:
        reasons.add_row(reason)
    console.print(reasons)

    table = Table(title="Regime Symbol Snapshots", box=box.SIMPLE_HEAVY)
    table.add_column("Symbol", style="bold")
    table.add_column("Bars", justify="right")
    table.add_column("Momentum", justify="right")
    table.add_column("Trend", justify="right")
    table.add_column("Volatility", justify="right")
    table.add_column("Drawdown", justify="right")
    table.add_column("Heat", justify="right")
    table.add_column("Quote Volume", justify="right")
    for item in assessment.snapshots:
        table.add_row(
            item.symbol,
            str(item.bars),
            f"{item.momentum_pct:.2f}%",
            f"{item.trend_pct:.2f}%",
            f"{item.volatility_pct:.2f}%",
            f"{item.drawdown_pct:.2f}%",
            f"{item.heat_score:.4f}",
            f"{item.quote_volume_usdt:.0f}",
        )
    console.print(table)
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(assessment.render_markdown(), encoding="utf-8")
        console.print(f"[green]Regime Markdown written:[/green] {markdown_output}")
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Regime JSON written:[/green] {json_output}")


@app.command("crypto-backtest")
def crypto_backtest(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to replay.",
    ),
    mainnet: bool = typer.Option(
        True,
        "--mainnet/--testnet",
        help="Use mainnet public candles or testnet public candles.",
    ),
    interval: Optional[str] = typer.Option(
        None,
        "--interval",
        help="Kline interval, e.g. 5m, 15m, 1h.",
    ),
    bars: int = typer.Option(
        500,
        "--bars",
        help="Historical candles to request per symbol.",
    ),
    lookback: int = typer.Option(
        120,
        "--lookback",
        help="Rolling lookback candles used by the scanner.",
    ),
    max_holding_bars: int = typer.Option(
        32,
        "--max-holding-bars",
        help="Maximum bars to hold a simulated trade.",
    ),
    fee_bps: float = typer.Option(
        4.0,
        "--fee-bps",
        help="Round-trip model uses this taker fee bps on entry and exit.",
    ),
    slippage_bps: float = typer.Option(
        2.0,
        "--slippage-bps",
        help="Adverse slippage bps applied to entry and exit fills.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable strategy fusion during replay.",
    ),
    lana: bool = typer.Option(
        False,
        "--lana/--no-lana",
        help="Enable Lana-inspired hot-mover logic during replay.",
    ),
    inventory_bridge: bool = typer.Option(
        False,
        "--inventory-bridge/--no-inventory-bridge",
        help="Enable the macro dead-inventory to micro float-inventory bridge.",
    ),
    dead_reserve_ratio: float = typer.Option(
        0.20,
        "--dead-reserve-ratio",
        help="Quote-equity ratio reserved for macro DCA inventory bridge buys.",
    ),
    inventory_dca_slices: int = typer.Option(
        12,
        "--inventory-dca-slices",
        help="Maximum macro DCA slices for the inventory bridge.",
    ),
    unlock_acceleration_threshold: float = typer.Option(
        0.002,
        "--unlock-acceleration-threshold",
        help="Normalized acceleration threshold required to unlock dead inventory.",
    ),
    inventory_sell_ratio: float = typer.Option(
        1.0,
        "--inventory-sell-ratio",
        help="Ratio of dead inventory to unlock and sell on each acceleration trigger.",
    ),
    bridge_macro_tick_bars: int = typer.Option(
        0,
        "--bridge-macro-tick-bars",
        help="Macro inventory bridge tick cadence in bars. Use 0 to spread DCA slices.",
    ),
    bridge_ema_anchor_bars: int = typer.Option(
        50,
        "--bridge-ema-anchor-bars",
        help="EMA anchor bars for the macro beta-deviation trigger.",
    ),
    bridge_beta_threshold: float = typer.Option(
        0.0,
        "--bridge-beta-threshold",
        help="Required discount versus EMA anchor before macro DCA buys.",
    ),
    bridge_moon_phase_pressure: float = typer.Option(
        0.0,
        "--bridge-moon-phase-pressure",
        help="Cyclical pressure multiplier applied to macro DCA budget.",
    ),
    bridge_deadline_force_pct: float = typer.Option(
        0.0,
        "--bridge-deadline-force-pct",
        help="Fallback ratio of remaining quote reserve to deploy when beta trigger is absent.",
    ),
    bridge_gc_threshold_bars: int = typer.Option(
        0,
        "--bridge-gc-threshold-bars",
        help="Bars after which stale dead inventory may be garbage-collected into float inventory.",
    ),
    bridge_gc_max_ratio: float = typer.Option(
        0.0,
        "--bridge-gc-max-ratio",
        help="Maximum dead inventory ratio released by stale GC unlock.",
    ),
):
    """Replay Hyperliquid candles through scanner, fusion, and risk gates."""

    from dataclasses import replace

    from tradingagents.crypto import (
        BacktestInventoryBridgeConfig,
        CryptoBacktester,
        CryptoTradingConfig,
    )

    if bars <= lookback + 2:
        raise typer.BadParameter("bars must be greater than lookback plus 2.")
    if lookback < 60:
        raise typer.BadParameter("lookback must be at least 60 for the scanner.")
    if max_holding_bars < 1:
        raise typer.BadParameter("max-holding-bars must be at least 1.")
    if not 0 <= dead_reserve_ratio <= 0.95:
        raise typer.BadParameter("dead-reserve-ratio must be between 0 and 0.95.")
    if inventory_dca_slices < 1:
        raise typer.BadParameter("inventory-dca-slices must be at least 1.")
    if unlock_acceleration_threshold < 0:
        raise typer.BadParameter("unlock-acceleration-threshold must be non-negative.")
    if not 0 <= inventory_sell_ratio <= 1:
        raise typer.BadParameter("inventory-sell-ratio must be between 0 and 1.")
    if bridge_macro_tick_bars < 0:
        raise typer.BadParameter("bridge-macro-tick-bars must be 0 or a positive integer.")
    if bridge_ema_anchor_bars < 2:
        raise typer.BadParameter("bridge-ema-anchor-bars must be at least 2.")
    if bridge_beta_threshold < 0:
        raise typer.BadParameter("bridge-beta-threshold must be non-negative.")
    if bridge_moon_phase_pressure < 0:
        raise typer.BadParameter("bridge-moon-phase-pressure must be non-negative.")
    if not 0 <= bridge_deadline_force_pct <= 1:
        raise typer.BadParameter("bridge-deadline-force-pct must be between 0 and 1.")
    if bridge_gc_threshold_bars < 0:
        raise typer.BadParameter("bridge-gc-threshold-bars must be 0 or a positive integer.")
    if not 0 <= bridge_gc_max_ratio <= 1:
        raise typer.BadParameter("bridge-gc-max-ratio must be between 0 and 1.")

    config = replace(
        CryptoTradingConfig.from_env(),
        exchange_provider="hyperliquid",
        hyperliquid_testnet=not mainnet,
        lookback_limit=lookback,
        strategy_fusion_enabled=fusion,
        lana_strategy_enabled=lana,
        hotlist_enabled=False,
    )
    if interval:
        config = replace(config, interval=interval)

    selected = tuple(item.strip().upper() for item in symbols.split(",") if item.strip())
    bridge_config = None
    if inventory_bridge:
        bridge_config = BacktestInventoryBridgeConfig(
            dead_reserve_ratio=dead_reserve_ratio,
            max_dca_slices=inventory_dca_slices,
            unlock_acceleration_threshold=unlock_acceleration_threshold,
            sell_ratio=inventory_sell_ratio,
            macro_tick_bars=bridge_macro_tick_bars,
            ema_anchor_bars=bridge_ema_anchor_bars,
            beta_threshold=bridge_beta_threshold,
            moon_phase_pressure=bridge_moon_phase_pressure,
            deadline_force_pct=bridge_deadline_force_pct,
            gc_threshold_bars=bridge_gc_threshold_bars,
            gc_max_ratio=bridge_gc_max_ratio,
        )
    report = CryptoBacktester(config).run(
        symbols=selected,
        bars=bars,
        max_holding_bars=max_holding_bars,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        inventory_bridge=bridge_config,
    )

    console.print(
        Panel(
            (
                f"symbols={','.join(report.symbols)} | interval={report.interval} | "
                f"bars={report.bars_requested} | lookback={report.lookback_limit} | "
                f"trades={len(report.trades)}"
            ),
            title="Hyperliquid Historical Replay",
            border_style="cyan",
        )
    )
    summary = Table(title="Backtest Summary", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Signals evaluated", str(report.signals_evaluated))
    summary.add_row("Risk rejected", str(report.risk_rejected))
    summary.add_row("Trades", str(len(report.trades)))
    summary.add_row("Wins", str(report.wins))
    summary.add_row("Losses", str(report.losses))
    summary.add_row("Win rate", f"{report.win_rate:.2%}")
    summary.add_row("Total PnL USDT", f"{report.total_pnl_usdt:.4f}")
    summary.add_row("Ending equity USDT", f"{report.ending_equity_usdt:.4f}")
    summary.add_row("Total return", f"{report.total_return_pct:.2f}%")
    summary.add_row("Max drawdown", f"{report.max_drawdown_pct:.2f}%")
    summary.add_row("Inventory events", str(len(report.inventory_events)))
    console.print(summary)

    if report.inventory_events:
        last_event = report.inventory_events[-1]
        inventory = Table(title="Inventory Bridge Ledger", box=box.SIMPLE_HEAVY)
        inventory.add_column("Metric")
        inventory.add_column("Value", justify="right")
        inventory.add_row(
            "Macro buys",
            str(sum(1 for event in report.inventory_events if event.event == "MACRO_DCA_BUY")),
        )
        inventory.add_row(
            "Acceleration unlocks",
            str(sum(1 for event in report.inventory_events if event.event == "ACCELERATION_UNLOCK")),
        )
        inventory.add_row(
            "GC unlocks",
            str(sum(1 for event in report.inventory_events if event.event == "GC_UNLOCK")),
        )
        inventory.add_row(
            "LOT_SIZE rejected",
            str(sum(1 for event in report.inventory_events if event.event == "LOT_SIZE_REJECTED")),
        )
        inventory.add_row(
            "Micro sells",
            str(sum(1 for event in report.inventory_events if event.event == "MICRO_SELL_FLOAT")),
        )
        inventory.add_row(
            "Inventory PnL USDT",
            f"{sum(event.realized_pnl_usdt for event in report.inventory_events):.4f}",
        )
        inventory.add_row("Ending dead qty", f"{last_event.dead_quantity:.8f}")
        inventory.add_row("Ending float qty", f"{last_event.float_quantity:.8f}")
        inventory.add_row("Ending quote USDT", f"{last_event.quote_balance_usdt:.4f}")
        console.print(inventory)

    trades = Table(title="Recent Simulated Trades", box=box.SIMPLE_HEAVY)
    trades.add_column("Symbol", style="bold")
    trades.add_column("Outcome")
    trades.add_column("Entry", justify="right")
    trades.add_column("Exit", justify="right")
    trades.add_column("Qty", justify="right")
    trades.add_column("PnL", justify="right")
    trades.add_column("PnL %", justify="right")
    trades.add_column("Bars", justify="right")
    trades.add_column("Confidence", justify="right")
    for trade in report.trades[-15:]:
        pnl_style = "green" if trade.pnl_usdt > 0 else "red"
        trades.add_row(
            trade.symbol,
            trade.outcome,
            f"{trade.entry_price:.4f}",
            f"{trade.exit_price:.4f}",
            f"{trade.quantity:.6f}",
            f"[{pnl_style}]{trade.pnl_usdt:.4f}[/{pnl_style}]",
            f"{trade.pnl_pct:.2f}%",
            str(trade.holding_bars),
            f"{trade.confidence:.2f}",
        )
    console.print(trades)


def _parse_text_tuple(raw: str, option_name: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not items:
        raise typer.BadParameter(f"{option_name} must include at least one value.")
    return items


def _parse_int_tuple(raw: str, option_name: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in raw.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            values.append(int(stripped))
        except ValueError as exc:
            raise typer.BadParameter(f"{option_name} contains a non-integer value: {stripped}") from exc
    if not values:
        raise typer.BadParameter(f"{option_name} must include at least one value.")
    return tuple(values)


def _backtest_sweep_rows(sweep_report, candidate_rules=None):
    rows = []
    for rank, result in enumerate(sweep_report.ranked_results, start=1):
        case = result.case
        report = result.report
        decision = result.evaluate_candidate(candidate_rules)
        rows.append(
            {
                "rank": rank,
                "candidate": "yes" if decision.approved else "no",
                "reject_reasons": "; ".join(decision.reasons),
                "symbols": ",".join(report.symbols),
                "interval": case.interval,
                "lookback": case.lookback_limit,
                "max_holding_bars": case.max_holding_bars,
                "bars": report.bars_requested,
                "signals_evaluated": report.signals_evaluated,
                "risk_rejected": report.risk_rejected,
                "trades": len(report.trades),
                "wins": report.wins,
                "losses": report.losses,
                "max_consecutive_losses": report.max_consecutive_losses,
                "win_rate": f"{report.win_rate:.6f}",
                "total_pnl_usdt": f"{report.total_pnl_usdt:.6f}",
                "total_return_pct": f"{report.total_return_pct:.6f}",
                "max_drawdown_pct": f"{report.max_drawdown_pct:.6f}",
                "risk_adjusted_score": f"{result.risk_adjusted_score:.6f}",
                "fee_bps": case.fee_bps,
                "slippage_bps": case.slippage_bps,
                "fusion_enabled": case.fusion_enabled,
                "lana_enabled": case.lana_enabled,
            }
        )
    return rows


def _write_backtest_sweep_csv(path: Path, sweep_report, candidate_rules=None) -> None:
    rows = _backtest_sweep_rows(sweep_report, candidate_rules)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else [
        "rank",
        "candidate",
        "reject_reasons",
        "symbols",
        "interval",
        "lookback",
        "max_holding_bars",
        "bars",
        "signals_evaluated",
        "risk_rejected",
        "trades",
        "wins",
        "losses",
        "max_consecutive_losses",
        "win_rate",
        "total_pnl_usdt",
        "total_return_pct",
        "max_drawdown_pct",
        "risk_adjusted_score",
        "fee_bps",
        "slippage_bps",
        "fusion_enabled",
        "lana_enabled",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_backtest_sweep_markdown(path: Path, sweep_report, top: int, candidate_rules=None) -> None:
    rows = _backtest_sweep_rows(sweep_report, candidate_rules)[:top]
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate_count = len(sweep_report.candidate_results(candidate_rules))
    lines = [
        "# Hyperliquid Backtest Sweep",
        "",
        f"- Symbols: {', '.join(sweep_report.symbols)}",
        f"- Bars per symbol: {sweep_report.bars_requested}",
        f"- Cases tested: {len(sweep_report.results)}",
        f"- Paper candidates: {candidate_count}",
        "",
        "| Rank | Candidate | Interval | Lookback | Hold bars | Trades | Win rate | Return | Max DD | Loss streak | PnL USDT | Score | Reject reasons |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {rank} | {candidate} | {interval} | {lookback} | {max_holding_bars} | {trades} | "
            "{win_rate:.2%} | {total_return_pct:.2f}% | {max_drawdown_pct:.2f}% | "
            "{max_consecutive_losses} | {total_pnl_usdt:.4f} | {risk_adjusted_score:.4f} | "
            "{reject_reasons} |".format(
                rank=row["rank"],
                candidate=row["candidate"],
                interval=row["interval"],
                lookback=row["lookback"],
                max_holding_bars=row["max_holding_bars"],
                trades=row["trades"],
                win_rate=float(row["win_rate"]),
                total_return_pct=float(row["total_return_pct"]),
                max_drawdown_pct=float(row["max_drawdown_pct"]),
                max_consecutive_losses=row["max_consecutive_losses"],
                total_pnl_usdt=float(row["total_pnl_usdt"]),
                risk_adjusted_score=float(row["risk_adjusted_score"]),
                reject_reasons=row["reject_reasons"] or "-",
            )
        )
    lines.extend(
        [
            "",
            "Notes:",
            "",
            "- This is a historical candle replay only.",
            "- It does not call the execution router or place orders.",
            "- The score is return percent minus max drawdown, scaled down when a case has fewer than five trades.",
            "- A paper candidate only means the case passed deterministic historical filters.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_evolution_report(base_path: Path, report, top: int) -> tuple[Path, Path, Path]:
    from tradingagents.crypto import (
        evolution_html_output_path,
        evolution_output_paths,
        render_evolution_record_html,
    )

    json_path, markdown_path = evolution_output_paths(base_path)
    html_path = evolution_html_output_path(base_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report.render_markdown(top=top), encoding="utf-8")
    record = {
        "candidate_id": report.challenger.candidate_id,
        "status": "challenger",
        "created_at": report.created_at,
        "promoted_at": None,
        "retired_at": None,
        "symbols": list(report.symbols),
        "interval": report.interval,
        "bars_requested": report.bars_requested,
        "population_size": report.population_size,
        "generations": report.generations,
        "seed": report.seed,
        "score": report.challenger.score.to_dict(),
        "generation_history": [item.to_dict() for item in report.generation_history],
        "crucible_windows": [item.to_dict() for item in report.crucible_windows],
        "population_plan": (
            report.population_plan.to_dict() if report.population_plan is not None else {}
        ),
        "run_context": dict(report.run_context),
        "candidate": report.challenger.to_dict(),
    }
    html_path.write_text(render_evolution_record_html(record), encoding="utf-8")
    return json_path, markdown_path, html_path


def _load_runtime_champion_or_default(
    config,
    mode: str,
    enabled: bool,
    season: str,
    archive_path: Optional[Path],
    symbols: tuple[str, ...] | None = None,
):
    if not enabled:
        return config, None
    if mode not in {"analysis", "paper"}:
        raise typer.BadParameter("evolution champion packages can only be loaded in analysis or paper mode.")

    from dataclasses import replace as dataclass_replace

    from tradingagents.crypto import EvolutionArchiveStore, RegimeEngine, load_runtime_champion_config

    store = EvolutionArchiveStore(config, archive_path)
    if store.current_champion() is None:
        console.print("[yellow]No evolution champion found; using built-in runtime defaults.[/yellow]")
        return config, None

    season_source = "manual"
    regime_assessment = None
    resolved_season = season.strip().lower()
    if resolved_season == "auto":
        try:
            regime_assessment = RegimeEngine(config).assess(
                symbols=symbols or config.symbols,
                interval=config.interval,
                bars=max(96, config.lookback_limit),
            )
        except (RuntimeError, ValueError) as exc:
            raise typer.BadParameter(f"auto champion season failed: {exc}") from exc
        resolved_season = regime_assessment.season
        season_source = "regime"

    try:
        application = load_runtime_champion_config(
            config,
            archive_path=archive_path,
            season=resolved_season,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if application is None:
        console.print("[yellow]No evolution champion found; using built-in runtime defaults.[/yellow]")
        return config, None
    if regime_assessment is not None:
        application = dataclass_replace(
            application,
            season_source=season_source,
            regime_assessment=regime_assessment.to_dict(),
        )
    return application.config, application


def _champion_loaded_message(application) -> str:
    message = (
        f"[green]Evolution champion loaded:[/green] {application.candidate_id} "
        f"season={application.season} source={application.season_source} "
        f"params={application.parameter_source} "
        f"settings={application.applied_settings}"
    )
    assessment = application.regime_assessment
    if isinstance(assessment, dict):
        message += (
            f" regime_score={float(assessment.get('heat_score', 0.0)):.4f} "
            f"regime_confidence={float(assessment.get('confidence', 0.0)):.2%}"
        )
    return message


@app.command("crypto-evolution-preset")
def crypto_evolution_preset(
    input_preset: Optional[Path] = typer.Option(
        None,
        "--input",
        help="Optional existing preset JSON to validate and preview.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Optional JSON path to write a normalized editable evolution preset.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the launch-state Markdown preview.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the editable launch-state HTML window.",
    ),
):
    """Show, validate, and optionally write the editable pre-evolution parameter state."""

    from tradingagents.crypto import (
        evolution_preset_sections,
        evolution_preset_template,
        render_evolution_preset_html,
        render_evolution_preset_markdown,
        validate_evolution_preset,
    )

    lab_preset = None
    validation = None
    if input_preset is not None:
        try:
            validation = validate_evolution_preset(input_preset)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        lab_preset = validation.preset
    sections = evolution_preset_sections(lab_preset)
    console.print(
        Panel(
            (
                "Review or edit this state before passing it to "
                "crypto-evolve with --preset."
            ),
            title="Evolution Preset Template",
            border_style="cyan",
        )
    )
    if input_preset is not None:
        console.print(f"[green]Preset input loaded:[/green] {input_preset}")
    if validation is not None:
        _print_evolution_preset_validation(validation)
    _print_evolution_launch_sections(sections)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(evolution_preset_template(lab_preset), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Evolution preset written:[/green] {output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_evolution_preset_markdown(lab_preset), encoding="utf-8")
        console.print(f"[green]Launch Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(render_evolution_preset_html(lab_preset), encoding="utf-8")
        console.print(f"[green]Launch HTML written:[/green] {html_output}")


@app.command("crypto-backtest-sweep")
def crypto_backtest_sweep(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to replay.",
    ),
    mainnet: bool = typer.Option(
        True,
        "--mainnet/--testnet",
        help="Use mainnet public candles or testnet public candles.",
    ),
    intervals: str = typer.Option(
        "5m,15m,1h",
        "--intervals",
        help="Comma-separated kline intervals to compare.",
    ),
    bars: int = typer.Option(
        500,
        "--bars",
        help="Historical candles to request per symbol per case.",
    ),
    lookbacks: str = typer.Option(
        "60,120",
        "--lookbacks",
        help="Comma-separated rolling lookback values.",
    ),
    max_holding_bars: str = typer.Option(
        "16,32,48",
        "--max-holding-bars",
        help="Comma-separated maximum holding bars to compare.",
    ),
    fee_bps: float = typer.Option(
        4.0,
        "--fee-bps",
        help="Taker fee bps charged on entry and exit.",
    ),
    slippage_bps: float = typer.Option(
        2.0,
        "--slippage-bps",
        help="Adverse slippage bps applied to entry and exit fills.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable strategy fusion during replay.",
    ),
    lana: bool = typer.Option(
        False,
        "--lana/--no-lana",
        help="Enable Lana-inspired hot-mover logic during replay.",
    ),
    top: int = typer.Option(
        10,
        "--top",
        help="Number of ranked cases to print and include in Markdown.",
    ),
    min_trades: int = typer.Option(
        5,
        "--min-trades",
        help="Minimum simulated trades required for a paper candidate.",
    ),
    min_win_rate: float = typer.Option(
        0.40,
        "--min-win-rate",
        help="Minimum win rate required for a paper candidate, e.g. 0.40.",
    ),
    min_return_pct: float = typer.Option(
        0.0,
        "--min-return-pct",
        help="Minimum total return percent required for a paper candidate.",
    ),
    max_drawdown_pct: float = typer.Option(
        5.0,
        "--max-drawdown-pct",
        help="Maximum drawdown percent allowed for a paper candidate.",
    ),
    max_consecutive_losses: int = typer.Option(
        3,
        "--max-consecutive-losses",
        help="Maximum loss streak allowed for a paper candidate.",
    ),
    candidates_only: bool = typer.Option(
        False,
        "--candidates-only/--all-results",
        help="Print only cases that pass the paper-candidate filters.",
    ),
    csv_output: Optional[Path] = typer.Option(
        None,
        "--csv-output",
        help="Optional CSV output path for all cases.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional Markdown output path for the ranked summary.",
    ),
):
    """Compare multiple Hyperliquid replay parameter sets."""

    from dataclasses import replace

    from tradingagents.crypto import (
        BacktestCandidateRules,
        CryptoBacktestSweepRunner,
        CryptoTradingConfig,
    )

    selected = tuple(item.upper() for item in _parse_text_tuple(symbols, "--symbols"))
    interval_options = _parse_text_tuple(intervals, "--intervals")
    lookback_options = _parse_int_tuple(lookbacks, "--lookbacks")
    holding_options = _parse_int_tuple(max_holding_bars, "--max-holding-bars")
    if any(lookback < 60 for lookback in lookback_options):
        raise typer.BadParameter("all lookbacks must be at least 60 for the scanner.")
    if any(holding < 1 for holding in holding_options):
        raise typer.BadParameter("all max-holding-bars values must be at least 1.")
    if bars <= max(lookback_options) + 2:
        raise typer.BadParameter("bars must be greater than the largest lookback plus 2.")
    if top < 1:
        raise typer.BadParameter("top must be at least 1.")
    if min_trades < 1:
        raise typer.BadParameter("min-trades must be at least 1.")
    if not 0 <= min_win_rate <= 1:
        raise typer.BadParameter("min-win-rate must be between 0 and 1.")
    if max_drawdown_pct < 0:
        raise typer.BadParameter("max-drawdown-pct must be non-negative.")
    if max_consecutive_losses < 0:
        raise typer.BadParameter("max-consecutive-losses must be non-negative.")

    config = replace(
        CryptoTradingConfig.from_env(),
        exchange_provider="hyperliquid",
        hyperliquid_testnet=not mainnet,
        interval=interval_options[0],
        lookback_limit=lookback_options[0],
        strategy_fusion_enabled=fusion,
        lana_strategy_enabled=lana,
        hotlist_enabled=False,
    )
    sweep_report = CryptoBacktestSweepRunner(config).run(
        symbols=selected,
        intervals=interval_options,
        lookbacks=lookback_options,
        max_holding_bars_options=holding_options,
        bars=bars,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        fusion_enabled=fusion,
        lana_enabled=lana,
    )
    candidate_rules = BacktestCandidateRules(
        min_trades=min_trades,
        min_win_rate=min_win_rate,
        min_total_return_pct=min_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_consecutive_losses=max_consecutive_losses,
    )
    candidate_results = sweep_report.candidate_results(candidate_rules)
    display_results = candidate_results if candidates_only else sweep_report.ranked_results

    best = sweep_report.best_candidate(candidate_rules) or sweep_report.best_result
    best_line = "none"
    if best is not None:
        best_line = (
            f"{best.case.interval} lookback={best.case.lookback_limit} "
            f"hold={best.case.max_holding_bars} score={best.risk_adjusted_score:.4f}"
        )
    console.print(
        Panel(
            (
                f"symbols={','.join(sweep_report.symbols)} | bars={sweep_report.bars_requested} | "
                f"cases={len(sweep_report.results)} | candidates={len(candidate_results)} | best={best_line}"
            ),
            title="Hyperliquid Backtest Sweep",
            border_style="cyan",
        )
    )

    table_title = f"Top {min(top, len(display_results))} Sweep Results"
    if candidates_only:
        table_title = f"Top {min(top, len(display_results))} Paper Candidates"
    table = Table(title=table_title, box=box.SIMPLE_HEAVY)
    table.add_column("Rank", justify="right")
    table.add_column("Cand")
    table.add_column("Interval")
    table.add_column("Lookback", justify="right")
    table.add_column("Hold", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Win", justify="right")
    table.add_column("Return", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Loss Streak", justify="right")
    table.add_column("PnL", justify="right")
    table.add_column("Score", justify="right")
    for rank, result in enumerate(display_results[:top], start=1):
        case = result.case
        report = result.report
        decision = result.evaluate_candidate(candidate_rules)
        pnl_style = "green" if report.total_pnl_usdt > 0 else "red"
        table.add_row(
            str(rank),
            "[green]yes[/green]" if decision.approved else "[red]no[/red]",
            case.interval,
            str(case.lookback_limit),
            str(case.max_holding_bars),
            str(len(report.trades)),
            f"{report.win_rate:.2%}",
            f"{report.total_return_pct:.2f}%",
            f"{report.max_drawdown_pct:.2f}%",
            str(report.max_consecutive_losses),
            f"[{pnl_style}]{report.total_pnl_usdt:.4f}[/{pnl_style}]",
            f"{result.risk_adjusted_score:.4f}",
        )
    console.print(table)
    if candidates_only and not display_results:
        console.print(
            Panel(
                (
                    "No cases passed the paper-candidate filters. "
                    "Inspect the CSV/Markdown output or loosen thresholds only for research."
                ),
                title="Candidate Filters",
                border_style="yellow",
            )
        )

    if csv_output is not None:
        _write_backtest_sweep_csv(csv_output, sweep_report, candidate_rules)
        console.print(f"[green]CSV written:[/green] {csv_output}")
    if markdown_output is not None:
        _write_backtest_sweep_markdown(markdown_output, sweep_report, top, candidate_rules)
        console.print(f"[green]Markdown written:[/green] {markdown_output}")


@app.command("crypto-evolve")
def crypto_evolve(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to evolve against.",
    ),
    mainnet: bool = typer.Option(
        True,
        "--mainnet/--testnet",
        help="Use mainnet public candles or testnet public candles.",
    ),
    interval: str = typer.Option(
        "5m",
        "--interval",
        help="Kline interval for the evolution lab replay.",
    ),
    bars: int = typer.Option(
        1000,
        "--bars",
        help="Historical candles to request per symbol.",
    ),
    population: int = typer.Option(
        10,
        "--population",
        help="Population size for the research-only GA loop.",
    ),
    generations: int = typer.Option(
        3,
        "--generations",
        help="Generation count for the research-only GA loop.",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help="Optional deterministic random seed.",
    ),
    fee_bps: float = typer.Option(
        20.0,
        "--fee-bps",
        help="Pessimistic taker fee bps charged on entry and exit.",
    ),
    slippage_bps: float = typer.Option(
        2.0,
        "--slippage-bps",
        help="Adverse slippage bps applied to entry and exit fills.",
    ),
    monte_carlo_simulations: int = typer.Option(
        250,
        "--monte-carlo-simulations",
        help="Monte Carlo paths for the final challenger review.",
    ),
    top: int = typer.Option(
        10,
        "--top",
        help="Number of ranked candidates to display and write to Markdown.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Optional output base path. Writes .json, .md, and .html challenger reports.",
    ),
    preset: Optional[Path] = typer.Option(
        None,
        "--preset",
        help="Optional editable evolution preset JSON from crypto-evolution-preset.",
    ),
    archive: bool = typer.Option(
        True,
        "--archive/--no-archive",
        help="Write the challenger into the local evolution archive.",
    ),
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
    archive_seeds: bool = typer.Option(
        True,
        "--archive-seeds/--no-archive-seeds",
        help="Inject champion/challenger genomes from the archive into the initial population.",
    ),
    job_index: bool = typer.Option(
        True,
        "--job-index/--no-job-index",
        help="Record this completed evolution run in the local job ledger.",
    ),
    job_index_path: Optional[Path] = typer.Option(
        None,
        "--job-index-path",
        help="Optional evolution job ledger JSON path.",
    ),
):
    """Run the research-only crypto evolution lab and emit a challenger package."""

    from dataclasses import replace

    from tradingagents.crypto import (
        CryptoEvolutionRunner,
        CryptoTradingConfig,
        EvolutionArchiveStore,
        EvolutionRunStore,
        evolution_preset_sections,
        load_archive_elite_genomes,
        load_archive_environment_anchor,
        validate_evolution_preset,
    )

    selected = tuple(item.upper() for item in _parse_text_tuple(symbols, "--symbols"))
    if bars < 260:
        raise typer.BadParameter("bars must be at least 260 so four season slices have history.")
    if population < 4:
        raise typer.BadParameter("population must be at least 4.")
    if generations < 1:
        raise typer.BadParameter("generations must be at least 1.")
    if fee_bps < 0:
        raise typer.BadParameter("fee-bps must be non-negative.")
    if slippage_bps < 0:
        raise typer.BadParameter("slippage-bps must be non-negative.")
    if monte_carlo_simulations < 1:
        raise typer.BadParameter("monte-carlo-simulations must be at least 1.")
    if top < 1:
        raise typer.BadParameter("top must be at least 1.")

    config = replace(
        CryptoTradingConfig.from_env(),
        exchange_provider="hyperliquid",
        hyperliquid_testnet=not mainnet,
        interval=interval,
        hyperliquid_max_leverage=1,
        lana_strategy_enabled=False,
        hotlist_enabled=False,
    )
    lab_preset = None
    validation = None
    if preset is not None:
        try:
            validation = validate_evolution_preset(preset)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        lab_preset = validation.preset
        console.print(
            Panel(
                f"preset={preset}",
                title="Pre-Evolution Launch State",
                border_style="cyan",
            )
        )
        _print_evolution_preset_validation(validation)
        _print_evolution_launch_sections(evolution_preset_sections(lab_preset))
    elite_seed_genomes = ()
    environment_anchor = None
    if archive_seeds:
        elite_seed_genomes = load_archive_elite_genomes(config, archive_path)
        environment_anchor = load_archive_environment_anchor(config, archive_path)
        if environment_anchor is not None:
            console.print(
                "[green]Environment anchor:[/green] current champion "
                f"dead_reserve_ratio={environment_anchor.dead_reserve_ratio:.4f} "
                f"global_stop_loss={environment_anchor.global_stop_loss:.4f}"
            )
    pre_run_context = {
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "monte_carlo_simulations": monte_carlo_simulations,
        "cli": {
            "mainnet": mainnet,
            "archive_enabled": archive,
            "archive_path": str(archive_path or config.state_dir / "evolution_archive.json"),
            "archive_seeds_enabled": archive_seeds,
            "environment_anchor_loaded": environment_anchor is not None,
            "output_base": str(output) if output is not None else None,
            "job_index_enabled": job_index,
            "job_index_path": str(job_index_path or config.state_dir / "evolution_runs.json"),
        },
        "preset": {
            "path": str(preset) if preset is not None else None,
            "used": validation is not None,
            "ok": validation.ok if validation is not None else None,
            "defaults_used": list(validation.defaults_used) if validation is not None else [],
            "projected_fields": list(validation.projected_fields) if validation is not None else [],
            "warnings": list(validation.warnings) if validation is not None else [],
        },
    }
    run_store = EvolutionRunStore(config, job_index_path) if job_index else None
    started_run_id = None
    if run_store is not None:
        started = run_store.record_started(
            symbols=selected,
            interval=config.interval,
            bars_requested=bars,
            population_size=population,
            generations=generations,
            seed=seed,
            run_context=pre_run_context,
        )
        started_run_id = str(started["run_id"])
        console.print(f"[green]Evolution job started:[/green] {started_run_id} -> {run_store.path}")
    try:
        report = CryptoEvolutionRunner(config).run(
            symbols=selected,
            bars=bars,
            population_size=population,
            generations=generations,
            seed=seed,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            environment=lab_preset.environment if lab_preset else None,
            environment_anchor=environment_anchor,
            season_regime=lab_preset.season_regime if lab_preset else None,
            seed_genome=lab_preset.seed_genome if lab_preset else None,
            elite_seed_genomes=elite_seed_genomes,
            monte_carlo_simulations=monte_carlo_simulations,
        )
    except Exception as exc:
        if run_store is not None and started_run_id is not None:
            run_store.record_failed(
                started_run_id,
                error=str(exc),
                error_type=type(exc).__name__,
                run_context=pre_run_context,
            )
            console.print(f"[red]Evolution job failed:[/red] {started_run_id} -> {run_store.path}")
        raise
    run_context = dict(report.run_context)
    run_context.update(
        {
            "cli": pre_run_context["cli"],
            "preset": pre_run_context["preset"],
        }
    )
    report = replace(report, run_context=run_context)
    challenger = report.challenger
    score = challenger.score

    console.print(
        Panel(
            (
                f"symbols={','.join(report.symbols)} | interval={report.interval} | "
                f"bars={report.bars_requested} | population={report.population_size} | "
                f"generations={report.generations} | challenger={challenger.candidate_id}"
            ),
            title="Hyperliquid Evolution Lab",
            border_style="cyan",
        )
    )
    if lab_preset is not None:
        loaded_parts = [
            name
            for name, value in (
                ("environment", lab_preset.environment),
                ("season_regime", lab_preset.season_regime),
                ("seed_genome", lab_preset.seed_genome),
            )
            if value is not None
        ]
        console.print(
            f"[green]Evolution preset loaded:[/green] {preset} "
            f"parts={','.join(loaded_parts) or '-'}"
        )
    if archive_seeds:
        console.print(
            f"[green]Archive elite seeds:[/green] {len(elite_seed_genomes)} "
            f"from {archive_path or config.state_dir / 'evolution_archive.json'}"
        )
    if report.population_plan is not None:
        plan = report.population_plan
        population_table = Table(title="Initial Population 1-4-5 Mix", box=box.SIMPLE_HEAVY)
        population_table.add_column("Source")
        population_table.add_column("Count", justify="right")
        population_table.add_column("Ratio", justify="right")
        population_table.add_row(
            "Incumbent elites",
            str(plan.incumbent_elites),
            f"{plan.incumbent_ratio:.2%}",
        )
        population_table.add_row(
            "Targeted mutants",
            str(plan.targeted_mutants),
            f"{plan.targeted_mutant_ratio:.2%}",
        )
        population_table.add_row("Explorers", str(plan.explorers), f"{plan.explorer_ratio:.2%}")
        population_table.add_row("Seed pool", str(plan.seed_pool_size), "-")
        population_table.add_row("Archive elite seeds", str(plan.elite_seed_count), "-")
        console.print(population_table)
    summary = Table(title="Challenger Score", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Score", f"{score.score:.4f}")
    summary.add_row("Alpha vs Ghost DCA", f"{score.alpha_pct:.2f}%")
    summary.add_row("Realized PnL USDT", f"{score.realized_pnl_usdt:.4f}")
    summary.add_row("Fee friction", f"{score.fee_friction_pct:.2f}%")
    summary.add_row("Max drawdown", f"{score.max_drawdown_pct:.2f}%")
    summary.add_row("Trades", str(score.trades))
    summary.add_row("Win rate", f"{score.win_rate:.2%}")
    summary.add_row("Risk rejected", str(score.risk_rejected))
    summary.add_row("Inventory events", str(score.inventory_event_count))
    summary.add_row("Inventory rejected", str(score.inventory_rejected))
    summary.add_row("Inventory PnL USDT", f"{score.inventory_realized_pnl_usdt:.4f}")
    console.print(summary)

    if challenger.final_review is not None:
        review = challenger.final_review
        mc = review.monte_carlo
        final_review = Table(title="Final Full-Sample Review", box=box.SIMPLE_HEAVY)
        final_review.add_column("Metric")
        final_review.add_column("Value", justify="right")
        final_review.add_row("Alpha", f"{review.alpha_pct:.2f}%")
        final_review.add_row("Return", f"{review.total_return_pct:.2f}%")
        final_review.add_row("Max drawdown", f"{review.max_drawdown_pct:.2f}%")
        final_review.add_row("Trades", str(review.trades))
        final_review.add_row("Inventory events", str(review.inventory_bridge.event_count))
        final_review.add_row("Inventory micro sells", str(review.inventory_bridge.micro_sells))
        final_review.add_row("Inventory PnL USDT", f"{review.inventory_bridge.realized_pnl_usdt:.4f}")
        final_review.add_row("MC P05 return", f"{mc.return_p05_pct:.2f}%")
        final_review.add_row("MC P50 return", f"{mc.return_p50_pct:.2f}%")
        final_review.add_row("MC P95 return", f"{mc.return_p95_pct:.2f}%")
        final_review.add_row("MC bankruptcy", f"{mc.bankruptcy_probability:.2%}")
        final_review.add_row("MC stop-loss breach", f"{mc.stop_loss_breach_probability:.2%}")
        final_review.add_row("Warnings", ", ".join(review.warnings) or "-")
        console.print(final_review)

    if report.generation_history:
        history = Table(title="Generation History", box=box.SIMPLE_HEAVY)
        history.add_column("Gen", justify="right")
        history.add_column("Best", justify="right")
        history.add_column("Avg", justify="right")
        history.add_column("Mut Prob", justify="right")
        history.add_column("Mut Scale", justify="right")
        history.add_column("Stale", justify="right")
        history.add_column("Improved")
        for item in report.generation_history:
            history.add_row(
                str(item.generation),
                f"{item.best_score:.4f}",
                f"{item.average_score:.4f}",
                f"{item.mutation_probability:.3f}",
                f"{item.mutation_scale:.3f}",
                str(item.stale_generations),
                "yes" if item.improved else "no",
            )
        console.print(history)

    if report.crucible_windows:
        windows = Table(title="Crucible Windows", box=box.SIMPLE_HEAVY)
        windows.add_column("Window")
        windows.add_column("Weight", justify="right")
        windows.add_column("Bars", justify="right")
        windows.add_column("Start")
        windows.add_column("End")
        for item in report.crucible_windows:
            windows.add_row(
                item.name,
                f"{item.weight:.2f}",
                str(item.bars),
                item.start_time or "-",
                item.end_time or "-",
            )
        console.print(windows)

    seasons = Table(title="Crucible Window / Season Friction", box=box.SIMPLE_HEAVY)
    seasons.add_column("Season")
    seasons.add_column("Multiplier", justify="right")
    seasons.add_column("Score", justify="right")
    seasons.add_column("Alpha", justify="right")
    seasons.add_column("Trades", justify="right")
    seasons.add_column("Inv PnL", justify="right")
    for season_score in score.seasons:
        seasons.add_row(
            season_score.name,
            f"{season_score.aggressiveness_multiplier:.2f}",
            f"{season_score.score:.4f}",
            f"{season_score.alpha_pct:.2f}%",
            str(season_score.trades),
            f"{season_score.inventory_realized_pnl_usdt:.4f}",
        )
    console.print(seasons)

    genome = challenger.genome
    genes = Table(title="Challenger LunarGenome", box=box.SIMPLE_HEAVY)
    genes.add_column("Gene")
    genes.add_column("Value", justify="right")
    for name, value in genome.to_dict().items():
        genes.add_row(name, f"{value:.6f}" if isinstance(value, float) else str(value))
    console.print(genes)

    ranked = Table(title=f"Top {min(top, len(report.ranked_candidates))} Candidates", box=box.SIMPLE_HEAVY)
    ranked.add_column("Rank", justify="right")
    ranked.add_column("Candidate")
    ranked.add_column("Score", justify="right")
    ranked.add_column("Alpha", justify="right")
    ranked.add_column("Max DD", justify="right")
    ranked.add_column("Trades", justify="right")
    for rank, candidate in enumerate(report.ranked_candidates[:top], start=1):
        candidate_score = candidate.score
        ranked.add_row(
            str(rank),
            candidate.candidate_id,
            f"{candidate_score.score:.4f}",
            f"{candidate_score.alpha_pct:.2f}%",
            f"{candidate_score.max_drawdown_pct:.2f}%",
            str(candidate_score.trades),
        )
    console.print(ranked)

    output_paths = {}
    if output is not None:
        json_path, markdown_path, html_path = _write_evolution_report(output, report, top)
        output_paths = {
            "json": str(json_path),
            "markdown": str(markdown_path),
            "html": str(html_path),
        }
        console.print(f"[green]Evolution JSON written:[/green] {json_path}")
        console.print(f"[green]Evolution Markdown written:[/green] {markdown_path}")
        console.print(f"[green]Evolution HTML written:[/green] {html_path}")
    archive_record = None
    archive_store_path = archive_path or config.state_dir / "evolution_archive.json"
    if archive:
        store = EvolutionArchiveStore(config, archive_path)
        archive_store_path = store.path
        archive_record = store.save_challenger(report)
        console.print(
            f"[green]Challenger archived:[/green] {archive_record['candidate_id']} -> {store.path}"
        )
    if job_index:
        if run_store is None:
            run_store = EvolutionRunStore(config, job_index_path)
        run_record = run_store.record_completed(
            report,
            output_paths=output_paths,
            archive_path=archive_store_path if archive else None,
            archived_candidate_id=archive_record["candidate_id"] if archive_record else None,
            run_id=started_run_id,
        )
        console.print(f"[green]Evolution job recorded:[/green] {run_record['run_id']} -> {run_store.path}")


@app.command("crypto-evolution-archive")
def crypto_evolution_archive(
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum archive records to display.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the archive dashboard JSON.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the archive dashboard Markdown.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the archive dashboard HTML.",
    ),
):
    """List local evolution challenger/champion records."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionArchiveStore,
        evolution_archive_sections,
        render_evolution_archive_html,
        render_evolution_archive_markdown,
    )

    if limit < 1:
        raise typer.BadParameter("limit must be at least 1.")
    config = CryptoTradingConfig.from_env()
    store = EvolutionArchiveStore(config, archive_path)
    data = store.load()
    sections = evolution_archive_sections(data, archive_path=store.path, limit=limit)
    records = list(reversed(data["records"]))[:limit]
    champion_id = data.get("champion_id") or "-"

    console.print(
        Panel(
            f"archive={store.path} | records={len(data['records'])} | champion={champion_id}",
            title="Evolution Archive",
            border_style="cyan",
        )
    )
    table = Table(title=f"Latest {len(records)} Records", box=box.SIMPLE_HEAVY)
    table.add_column("Status")
    table.add_column("Candidate")
    table.add_column("Symbols")
    table.add_column("Interval")
    table.add_column("Score", justify="right")
    table.add_column("Alpha", justify="right")
    table.add_column("Created")
    table.add_column("Promoted")
    for record in records:
        score = record.get("score", {})
        status = record.get("status", "-")
        style = "green" if status == "champion" else "yellow" if status == "challenger" else "dim"
        table.add_row(
            f"[{style}]{status}[/{style}]",
            str(record.get("candidate_id", "-")),
            ",".join(record.get("symbols", [])),
            str(record.get("interval", "-")),
            f"{float(score.get('score', 0.0)):.4f}",
            f"{float(score.get('alpha_pct', 0.0)):.2f}%",
            str(record.get("created_at", "-")),
            str(record.get("promoted_at") or "-"),
        )
    console.print(table)
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(sections, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Archive dashboard JSON written:[/green] {json_output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(
            render_evolution_archive_markdown(data, archive_path=store.path, limit=limit),
            encoding="utf-8",
        )
        console.print(f"[green]Archive dashboard Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(
            render_evolution_archive_html(data, archive_path=store.path, limit=limit),
            encoding="utf-8",
        )
        console.print(f"[green]Archive dashboard HTML written:[/green] {html_output}")


@app.command("crypto-evolution-jobs")
def crypto_evolution_jobs(
    job_index_path: Optional[Path] = typer.Option(
        None,
        "--job-index-path",
        help="Optional evolution job ledger JSON path.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum jobs to display.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the job dashboard JSON.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the job dashboard Markdown.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the job dashboard HTML.",
    ),
):
    """List evolution research jobs."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionRunStore,
        evolution_runs_sections,
        render_evolution_runs_html,
        render_evolution_runs_markdown,
    )

    if limit < 1:
        raise typer.BadParameter("limit must be at least 1.")
    config = CryptoTradingConfig.from_env()
    store = EvolutionRunStore(config, job_index_path)
    data = store.load()
    sections = evolution_runs_sections(data, ledger_path=store.path, limit=limit)
    runs = sections["runs"]

    console.print(
        Panel(
            f"ledger={store.path} | runs={sections['identity']['run_count']}",
            title="Evolution Jobs",
            border_style="cyan",
        )
    )
    table = Table(title=f"Latest {len(runs)} Jobs", box=box.SIMPLE_HEAVY)
    table.add_column("Status")
    table.add_column("Run")
    table.add_column("Challenger")
    table.add_column("Symbols")
    table.add_column("Score", justify="right")
    table.add_column("Alpha", justify="right")
    table.add_column("Warnings", justify="right")
    table.add_column("Completed")
    table.add_column("Error")
    for run in runs:
        table.add_row(
            str(run.get("status", "-")),
            str(run.get("run_id", "-")),
            str(run.get("challenger_id") or "-"),
            ",".join(run.get("symbols", [])),
            f"{float(run.get('score') or 0.0):.4f}",
            f"{float(run.get('alpha_pct') or 0.0):.2f}%",
            str(run.get("warning_count", 0)),
            str(run.get("completed_at") or "-"),
            str(run.get("error_message") or "-"),
        )
    console.print(table)
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(sections, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Jobs dashboard JSON written:[/green] {json_output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(
            render_evolution_runs_markdown(data, ledger_path=store.path, limit=limit),
            encoding="utf-8",
        )
        console.print(f"[green]Jobs dashboard Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(
            render_evolution_runs_html(data, ledger_path=store.path, limit=limit),
            encoding="utf-8",
        )
        console.print(f"[green]Jobs dashboard HTML written:[/green] {html_output}")


@app.command("crypto-evolution-job-inspect")
def crypto_evolution_job_inspect(
    run_id: Optional[str] = typer.Argument(
        None,
        help="Evolution run id to inspect. Defaults to the latest job.",
    ),
    job_index_path: Optional[Path] = typer.Option(
        None,
        "--job-index-path",
        help="Optional evolution job ledger JSON path.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the layered job JSON.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the job Markdown report.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the read-only job HTML window.",
    ),
):
    """Inspect one evolution job state record."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionRunStore,
        evolution_run_sections,
        render_evolution_run_html,
        render_evolution_run_markdown,
    )

    store = EvolutionRunStore(CryptoTradingConfig.from_env(), job_index_path)
    data = store.load()
    runs = [item for item in data.get("runs", []) if isinstance(item, dict)]
    if run_id:
        record = next((item for item in runs if item.get("run_id") == run_id), None)
        if record is None:
            raise typer.BadParameter(f"run {run_id} was not found in the evolution job ledger.")
    else:
        if not runs:
            raise typer.BadParameter("no evolution jobs found.")
        record = runs[-1]

    sections = evolution_run_sections(record, ledger_path=store.path)
    identity = sections["identity"]
    console.print(
        Panel(
            (
                f"run={identity.get('run_id')} | status={identity.get('status')} | "
                f"challenger={identity.get('challenger_id') or '-'}"
            ),
            title="Evolution Job State",
            border_style="cyan",
        )
    )
    _print_key_value_table("Job Identity", identity)
    if sections["score"]:
        _print_key_value_table("Score", sections["score"])
    if sections["outputs"]:
        _print_key_value_table("Outputs", sections["outputs"])
    if sections["archive"]:
        _print_key_value_table("Archive", sections["archive"])
    if sections["run_context"]:
        _print_key_value_table("Run Context", _flatten_cli_mapping(sections["run_context"]))
    if sections["error"].get("type") or sections["error"].get("message"):
        _print_key_value_table("Error", sections["error"])
    _print_key_value_table("Safety", sections["safety"])

    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(sections, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Job state JSON written:[/green] {json_output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(
            render_evolution_run_markdown(record, ledger_path=store.path),
            encoding="utf-8",
        )
        console.print(f"[green]Job state Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(
            render_evolution_run_html(record, ledger_path=store.path),
            encoding="utf-8",
        )
        console.print(f"[green]Job state HTML written:[/green] {html_output}")


@app.command("crypto-evolution-inspect")
def crypto_evolution_inspect(
    candidate_id: Optional[str] = typer.Argument(
        None,
        help="Candidate id to inspect. Defaults to the current champion.",
    ),
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the layered package JSON.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the package Markdown report.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the read-only package HTML window.",
    ),
):
    """Inspect one evolution package as environment, season, genes, and score."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionArchiveStore,
        evolution_record_sections,
        render_evolution_record_html,
        render_evolution_record_markdown,
    )

    store = EvolutionArchiveStore(CryptoTradingConfig.from_env(), archive_path)
    if candidate_id:
        record = store.find(candidate_id)
    else:
        record = store.current_champion()
    if record is None:
        target = candidate_id or "current champion"
        raise typer.BadParameter(f"{target} was not found in the evolution archive.")

    try:
        sections = evolution_record_sections(record)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    identity = sections["identity"]
    score = sections["score"]
    console.print(
        Panel(
            (
                f"id={identity['candidate_id']} | status={identity['status']} | "
                f"symbols={','.join(identity.get('symbols') or [])} | "
                f"interval={identity['interval']} | score={float(score.get('score', 0.0)):.4f}"
            ),
            title="Evolution Package State",
            border_style="cyan",
        )
    )
    _print_key_value_table("Environment", sections["environment"])

    seasons = Table(title="Season Regime", box=box.SIMPLE_HEAVY)
    seasons.add_column("Season")
    seasons.add_column("Aggressiveness", justify="right")
    for row in sections["season_regime"].get("seasons", []):
        if isinstance(row, dict):
            seasons.add_row(
                str(row.get("name", "-")),
                f"{float(row.get('aggressiveness_multiplier', 0.0)):.2f}",
            )
    seasons.add_row("tick_offset_minutes", str(sections["season_regime"].get("tick_offset_minutes", 0)))
    console.print(seasons)

    _print_key_value_table("Macro Genes", sections["macro_genes"])
    _print_key_value_table("Timing Genes", sections["timing_genes"])
    _print_key_value_table("Micro Genes", sections["micro_genes"])
    _print_key_value_table(
        "Score",
        {
            "score": score.get("score"),
            "alpha_pct": score.get("alpha_pct"),
            "realized_pnl_usdt": score.get("realized_pnl_usdt"),
            "fee_friction_pct": score.get("fee_friction_pct"),
            "max_drawdown_pct": score.get("max_drawdown_pct"),
            "trades": score.get("trades"),
            "win_rate": score.get("win_rate"),
            "max_consecutive_losses": score.get("max_consecutive_losses"),
            "risk_rejected": score.get("risk_rejected"),
            "inventory_event_count": score.get("inventory_event_count"),
            "inventory_rejected": score.get("inventory_rejected"),
            "inventory_realized_pnl_usdt": score.get("inventory_realized_pnl_usdt"),
        },
    )
    generation_history = sections.get("generation_history", [])
    if generation_history:
        history = Table(title="Generation History", box=box.SIMPLE_HEAVY)
        history.add_column("Gen", justify="right")
        history.add_column("Best", justify="right")
        history.add_column("Avg", justify="right")
        history.add_column("Mut Prob", justify="right")
        history.add_column("Mut Scale", justify="right")
        history.add_column("Stale", justify="right")
        history.add_column("Improved")
        for item in generation_history:
            if isinstance(item, dict):
                history.add_row(
                    str(item.get("generation", "-")),
                    _format_state_value(item.get("best_score")),
                    _format_state_value(item.get("average_score")),
                    _format_state_value(item.get("mutation_probability")),
                    _format_state_value(item.get("mutation_scale")),
                    str(item.get("stale_generations", "-")),
                    "yes" if item.get("improved") else "no",
                )
        console.print(history)
    population_plan = sections.get("population_plan", {})
    if isinstance(population_plan, dict) and population_plan:
        _print_key_value_table("Population Initialization", population_plan)
    crucible_windows = sections.get("crucible_windows", [])
    if crucible_windows:
        windows = Table(title="Crucible Windows", box=box.SIMPLE_HEAVY)
        windows.add_column("Window")
        windows.add_column("Weight", justify="right")
        windows.add_column("Bars", justify="right")
        windows.add_column("Start")
        windows.add_column("End")
        for item in crucible_windows:
            if isinstance(item, dict):
                windows.add_row(
                    str(item.get("name", "-")),
                    _format_state_value(item.get("weight")),
                    _format_state_value(item.get("bars")),
                    str(item.get("start_time") or "-"),
                    str(item.get("end_time") or "-"),
                )
        console.print(windows)
    final_review = sections.get("final_review", {})
    if final_review:
        _print_key_value_table(
            "Final Full-Sample Review",
            {
                "alpha_pct": final_review.get("alpha_pct"),
                "total_return_pct": final_review.get("total_return_pct"),
                "max_drawdown_pct": final_review.get("max_drawdown_pct"),
                "trades": final_review.get("trades"),
                "win_rate": final_review.get("win_rate"),
                "risk_rejected": final_review.get("risk_rejected"),
            },
        )
        inventory_bridge = final_review.get("inventory_bridge", {})
        if isinstance(inventory_bridge, dict):
            _print_key_value_table("Inventory Bridge", inventory_bridge)
        monte_carlo = final_review.get("monte_carlo", {})
        if isinstance(monte_carlo, dict):
            _print_key_value_table("Monte Carlo Review", monte_carlo)
        warnings = final_review.get("warnings") or ()
        if warnings:
            console.print("[yellow]Final review warnings:[/yellow] " + ", ".join(warnings))

    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(sections, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Layered JSON written:[/green] {json_output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_evolution_record_markdown(record), encoding="utf-8")
        console.print(f"[green]Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(render_evolution_record_html(record), encoding="utf-8")
        console.print(f"[green]HTML written:[/green] {html_output}")


@app.command("crypto-evolution-compare")
def crypto_evolution_compare(
    candidate_id: str = typer.Argument(..., help="Candidate id to compare against a baseline."),
    baseline_id: Optional[str] = typer.Option(
        None,
        "--baseline-id",
        help="Baseline candidate id. Defaults to the current champion.",
    ),
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the comparison JSON.",
    ),
    markdown_output: Optional[Path] = typer.Option(
        None,
        "--markdown-output",
        help="Optional path to write the comparison Markdown.",
    ),
    html_output: Optional[Path] = typer.Option(
        None,
        "--html-output",
        help="Optional path to write the comparison HTML.",
    ),
):
    """Compare a challenger or package against the current champion."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionArchiveStore,
        evolution_compare_sections,
        render_evolution_compare_html,
        render_evolution_compare_markdown,
    )

    store = EvolutionArchiveStore(CryptoTradingConfig.from_env(), archive_path)
    candidate = store.find(candidate_id)
    if candidate is None:
        raise typer.BadParameter(f"{candidate_id} was not found in the evolution archive.")
    baseline = store.find(baseline_id) if baseline_id else store.current_champion()
    baseline_label = baseline_id or "current champion"
    if baseline is None:
        raise typer.BadParameter(f"{baseline_label} was not found in the evolution archive.")

    try:
        sections = evolution_compare_sections(candidate, baseline)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    identity = sections["identity"]
    review = sections.get("promotion_review", {})
    console.print(
        Panel(
            (
                f"candidate={identity['candidate_id']} ({identity['candidate_status']}) | "
                f"baseline={identity['baseline_id']} ({identity['baseline_status']}) | "
                "delta=candidate-baseline"
            ),
            title="Evolution Package Comparison",
            border_style="cyan",
        )
    )
    if isinstance(review, dict) and review:
        _print_key_value_table(
            "Promotion Review",
            {
                "verdict": review.get("verdict"),
                "blocks_promotion": review.get("blocks_promotion"),
                "score_delta": review.get("score_delta"),
                "alpha_delta_pct": review.get("alpha_delta_pct"),
                "max_drawdown_delta_pct": review.get("max_drawdown_delta_pct"),
                "fee_friction_delta_pct": review.get("fee_friction_delta_pct"),
                "trade_delta": review.get("trade_delta"),
                "reasons": ", ".join(review.get("reasons", [])),
            },
        )
    for title, key in (
        ("Score Delta", "score_delta"),
        ("Environment Delta", "environment_delta"),
        ("Season Delta", "season_delta"),
        ("Macro Gene Delta", "macro_gene_delta"),
        ("Timing Gene Delta", "timing_gene_delta"),
        ("Micro Gene Delta", "micro_gene_delta"),
    ):
        rows = sections.get(key, [])
        if rows:
            _print_compare_table(title, rows)

    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(sections, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Comparison JSON written:[/green] {json_output}")
    if markdown_output is not None:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_evolution_compare_markdown(candidate, baseline), encoding="utf-8")
        console.print(f"[green]Comparison Markdown written:[/green] {markdown_output}")
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
        html_output.write_text(render_evolution_compare_html(candidate, baseline), encoding="utf-8")
        console.print(f"[green]Comparison HTML written:[/green] {html_output}")


def _print_compare_table(title: str, rows: list[dict]) -> None:
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Parameter")
    table.add_column("Candidate", justify="right")
    table.add_column("Baseline", justify="right")
    table.add_column("Delta", justify="right")
    for row in rows:
        table.add_row(
            str(row.get("name", "-")),
            _format_state_value(row.get("candidate")),
            _format_state_value(row.get("baseline")),
            _format_state_value(row.get("delta")),
        )
    console.print(table)


def _print_key_value_table(title: str, payload: dict) -> None:
    table = Table(title=title, box=box.SIMPLE_HEAVY)
    table.add_column("Parameter")
    table.add_column("Value", justify="right")
    for name, value in payload.items():
        table.add_row(str(name), _format_state_value(value))
    console.print(table)


def _format_state_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    if value is None:
        return "-"
    return str(value)


def _flatten_cli_mapping(payload: dict, prefix: str = "") -> dict:
    flattened = {}
    for name, value in payload.items():
        key = f"{prefix}.{name}" if prefix else str(name)
        if isinstance(value, dict):
            flattened.update(_flatten_cli_mapping(value, key))
        elif isinstance(value, (list, tuple)):
            flattened[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            flattened[key] = value
    return flattened


def _print_evolution_launch_sections(sections: dict) -> None:
    identity = sections.get("identity", {})
    defaults_used = identity.get("defaults_used") or []
    _print_key_value_table(
        "Launch Identity",
        {
            "kind": identity.get("kind", "evolution_preset"),
            "version": identity.get("version", 1),
            "defaults_used": ", ".join(defaults_used) or "-",
        },
    )
    _print_key_value_table("Environment", sections["environment"])

    seasons = Table(title="Season Regime", box=box.SIMPLE_HEAVY)
    seasons.add_column("Season")
    seasons.add_column("Aggressiveness", justify="right")
    for row in sections["season_regime"]["seasons"]:
        seasons.add_row(
            str(row["name"]),
            f"{float(row['aggressiveness_multiplier']):.2f}",
        )
    seasons.add_row(
        "tick_offset_minutes",
        str(sections["season_regime"]["tick_offset_minutes"]),
    )
    console.print(seasons)

    _print_key_value_table("Macro Genes", sections["macro_genes"])
    _print_key_value_table("Timing Genes", sections["timing_genes"])
    _print_key_value_table("Micro Genes", sections["micro_genes"])
    _print_key_value_table("Safety", sections["safety"])


def _print_evolution_preset_validation(validation) -> None:
    status = "ok" if validation.ok else "review"
    border = "green" if validation.ok else "yellow"
    console.print(
        Panel(
            (
                f"status={status} | defaults={len(validation.defaults_used)} | "
                f"projected={len(validation.projected_fields)} | warnings={len(validation.warnings)}"
            ),
            title="Preset Validation",
            border_style=border,
        )
    )
    if validation.warnings:
        table = Table(title="Validation Warnings", box=box.SIMPLE_HEAVY)
        table.add_column("Warning")
        for warning in validation.warnings:
            table.add_row(str(warning))
        console.print(table)
    if validation.projected_fields:
        table = Table(title="Projected Inputs", box=box.SIMPLE_HEAVY)
        table.add_column("Field")
        for field in validation.projected_fields:
            table.add_row(str(field))
        console.print(table)


@app.command("crypto-evolution-promote")
def crypto_evolution_promote(
    candidate_id: str = typer.Argument(..., help="Challenger candidate id to promote."),
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
):
    """Promote one archived challenger to champion and retire the old champion."""

    from tradingagents.crypto import CryptoTradingConfig, EvolutionArchiveStore

    config = CryptoTradingConfig.from_env()
    store = EvolutionArchiveStore(config, archive_path)
    try:
        result = store.promote(candidate_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    score = result.promoted.get("score", {})
    retired_id = result.retired.get("candidate_id") if result.retired else "-"
    console.print(
        Panel(
            (
                f"champion={result.candidate_id} | retired={retired_id} | "
                f"archive={result.archive_path}"
            ),
            title="Evolution Champion Promoted",
            border_style="green",
        )
    )
    if result.promotion_review is not None:
        _print_key_value_table(
            "Promotion Review At Promote",
            {
                "verdict": result.promotion_review.get("verdict"),
                "blocks_promotion": result.promotion_review.get("blocks_promotion"),
                "baseline_candidate_id": result.promotion_review.get("baseline_candidate_id"),
                "score_delta": result.promotion_review.get("score_delta"),
                "alpha_delta_pct": result.promotion_review.get("alpha_delta_pct"),
                "max_drawdown_delta_pct": result.promotion_review.get("max_drawdown_delta_pct"),
                "fee_friction_delta_pct": result.promotion_review.get("fee_friction_delta_pct"),
                "trade_delta": result.promotion_review.get("trade_delta"),
                "reasons": ", ".join(result.promotion_review.get("reasons", [])),
            },
        )
    else:
        console.print("[yellow]Promotion review skipped:[/yellow] no previous champion baseline.")
    if result.champion_cache_path is not None:
        console.print(f"[green]Champion cache refreshed:[/green] {result.champion_cache_path}")
    summary = Table(title="Champion Package", box=box.SIMPLE_HEAVY)
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Score", f"{float(score.get('score', 0.0)):.4f}")
    summary.add_row("Alpha", f"{float(score.get('alpha_pct', 0.0)):.2f}%")
    summary.add_row("Trades", str(score.get("trades", 0)))
    summary.add_row("Max drawdown", f"{float(score.get('max_drawdown_pct', 0.0)):.2f}%")
    summary.add_row("Status", result.promoted.get("status", "champion"))
    console.print(summary)


@app.command("crypto-evolution-champion-cache")
def crypto_evolution_champion_cache(
    archive_path: Optional[Path] = typer.Option(
        None,
        "--archive-path",
        help="Optional evolution archive JSON path.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Refresh the local champion cache from the current archive champion.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Optional path to copy the champion cache JSON payload.",
    ),
):
    """Show or refresh the read-only champion cache used by runtime instances."""

    from tradingagents.crypto import (
        CryptoTradingConfig,
        EvolutionArchiveStore,
        evolution_champion_cache_path,
    )

    config = CryptoTradingConfig.from_env()
    store = EvolutionArchiveStore(config, archive_path)
    cache_path = evolution_champion_cache_path(config, store.path)
    payload = None
    if refresh:
        payload = store.write_champion_cache()
        if payload is None:
            raise typer.BadParameter("current champion was not found in the evolution archive.")
    elif cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        payload = store.write_champion_cache()
        if payload is None:
            raise typer.BadParameter("champion cache is missing and no champion exists to refresh it.")

    console.print(
        Panel(
            (
                f"candidate={payload.get('candidate_id')} | status={payload.get('status')} | "
                f"cache={cache_path}"
            ),
            title="Evolution Champion Cache",
            border_style="cyan",
        )
    )
    _print_key_value_table(
        "Champion Cache",
        {
            "candidate_id": payload.get("candidate_id"),
            "cached_at": payload.get("cached_at"),
            "promoted_at": payload.get("promoted_at"),
            "symbols": ",".join(payload.get("symbols", [])),
            "interval": payload.get("interval"),
            "places_live_orders": payload.get("safety", {}).get("places_live_orders"),
            "runtime_modes_allowed": ",".join(payload.get("safety", {}).get("runtime_modes_allowed", [])),
        },
    )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Champion cache copied:[/green] {output}")


@app.command("crypto-paper-queue")
def crypto_paper_queue(
    symbols: str = typer.Option(
        "BTC,ETH,SOL,HYPE",
        "--symbols",
        help="Comma-separated Hyperliquid coins to replay before queueing.",
    ),
    mainnet: bool = typer.Option(
        True,
        "--mainnet/--testnet",
        help="Use mainnet public candles or testnet public candles for candidate screening.",
    ),
    intervals: str = typer.Option(
        "5m,15m,1h",
        "--intervals",
        help="Comma-separated kline intervals to compare.",
    ),
    bars: int = typer.Option(
        800,
        "--bars",
        help="Historical candles to request per symbol per case.",
    ),
    lookbacks: str = typer.Option(
        "60,120",
        "--lookbacks",
        help="Comma-separated rolling lookback values.",
    ),
    max_holding_bars: str = typer.Option(
        "16,32,48",
        "--max-holding-bars",
        help="Comma-separated maximum historical holding bars to compare.",
    ),
    fee_bps: float = typer.Option(
        4.0,
        "--fee-bps",
        help="Taker fee bps charged on entry and exit during historical replay.",
    ),
    slippage_bps: float = typer.Option(
        2.0,
        "--slippage-bps",
        help="Adverse slippage bps applied to entry and exit fills during replay.",
    ),
    fusion: bool = typer.Option(
        True,
        "--fusion/--no-fusion",
        help="Enable or disable strategy fusion during replay and queued paper commands.",
    ),
    lana: bool = typer.Option(
        False,
        "--lana/--no-lana",
        help="Enable Lana-inspired hot-mover logic during replay and queued paper commands.",
    ),
    top: int = typer.Option(
        3,
        "--top",
        help="Maximum paper candidates to queue.",
    ),
    min_trades: int = typer.Option(
        5,
        "--min-trades",
        help="Minimum simulated trades required for a paper candidate.",
    ),
    min_win_rate: float = typer.Option(
        0.40,
        "--min-win-rate",
        help="Minimum win rate required for a paper candidate, e.g. 0.40.",
    ),
    min_return_pct: float = typer.Option(
        0.0,
        "--min-return-pct",
        help="Minimum total return percent required for a paper candidate.",
    ),
    max_drawdown_pct: float = typer.Option(
        5.0,
        "--max-drawdown-pct",
        help="Maximum drawdown percent allowed for a paper candidate.",
    ),
    max_consecutive_losses: int = typer.Option(
        3,
        "--max-consecutive-losses",
        help="Maximum loss streak allowed for a paper candidate.",
    ),
    interval_seconds: int = typer.Option(
        300,
        "--interval-seconds",
        help="Interval seconds to place in generated paper autopilot commands.",
    ),
    cycles: int = typer.Option(
        0,
        "--cycles",
        help="Cycle count to place in generated paper autopilot commands. Use 0 for service loop.",
    ),
    ai_review: bool = typer.Option(
        False,
        "--ai-review",
        help="Include --ai-review in generated paper autopilot commands.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Output base path. Defaults to state_dir/paper_queue.",
    ),
):
    """Build a paper-only entry queue from backtest-approved candidates."""

    from dataclasses import replace

    from tradingagents.crypto import (
        BacktestCandidateRules,
        CryptoBacktestSweepRunner,
        CryptoTradingConfig,
        build_paper_queue_plan,
        paper_queue_output_paths,
    )

    selected = tuple(item.upper() for item in _parse_text_tuple(symbols, "--symbols"))
    interval_options = _parse_text_tuple(intervals, "--intervals")
    lookback_options = _parse_int_tuple(lookbacks, "--lookbacks")
    holding_options = _parse_int_tuple(max_holding_bars, "--max-holding-bars")
    if any(lookback < 60 for lookback in lookback_options):
        raise typer.BadParameter("all lookbacks must be at least 60 for the scanner.")
    if any(holding < 1 for holding in holding_options):
        raise typer.BadParameter("all max-holding-bars values must be at least 1.")
    if bars <= max(lookback_options) + 2:
        raise typer.BadParameter("bars must be greater than the largest lookback plus 2.")
    if top < 1:
        raise typer.BadParameter("top must be at least 1.")
    if min_trades < 1:
        raise typer.BadParameter("min-trades must be at least 1.")
    if not 0 <= min_win_rate <= 1:
        raise typer.BadParameter("min-win-rate must be between 0 and 1.")
    if max_drawdown_pct < 0:
        raise typer.BadParameter("max-drawdown-pct must be non-negative.")
    if max_consecutive_losses < 0:
        raise typer.BadParameter("max-consecutive-losses must be non-negative.")
    if interval_seconds < 1:
        raise typer.BadParameter("interval-seconds must be at least 1.")
    if cycles < 0:
        raise typer.BadParameter("cycles must be 0 or a positive integer.")

    config = replace(
        CryptoTradingConfig.from_env(),
        exchange_provider="hyperliquid",
        hyperliquid_testnet=not mainnet,
        interval=interval_options[0],
        lookback_limit=lookback_options[0],
        strategy_fusion_enabled=fusion,
        lana_strategy_enabled=lana,
        hotlist_enabled=False,
    )
    candidate_rules = BacktestCandidateRules(
        min_trades=min_trades,
        min_win_rate=min_win_rate,
        min_total_return_pct=min_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_consecutive_losses=max_consecutive_losses,
    )
    sweep_report = CryptoBacktestSweepRunner(config).run(
        symbols=selected,
        intervals=interval_options,
        lookbacks=lookback_options,
        max_holding_bars_options=holding_options,
        bars=bars,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        fusion_enabled=fusion,
        lana_enabled=lana,
    )
    plan = build_paper_queue_plan(
        sweep_report,
        candidate_rules,
        top=top,
        interval_seconds=interval_seconds,
        cycles=cycles,
        ai_review=ai_review,
    )
    json_path, markdown_path = paper_queue_output_paths(output or (config.state_dir / "paper_queue"))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(plan.render_markdown(), encoding="utf-8")

    console.print(
        Panel(
            (
                f"symbols={','.join(selected)} | cases={len(sweep_report.results)} | "
                f"queued={plan.ready_count} | mode=paper only"
            ),
            title="Hyperliquid Paper Entry Queue",
            border_style="cyan",
        )
    )
    table = Table(title=f"Queued Paper Candidates: {plan.ready_count}", box=box.SIMPLE_HEAVY)
    table.add_column("Rank", justify="right")
    table.add_column("Symbols")
    table.add_column("Interval")
    table.add_column("Lookback", justify="right")
    table.add_column("Hold", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Win", justify="right")
    table.add_column("Return", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Score", justify="right")
    for item in plan.items:
        table.add_row(
            str(item.rank),
            ",".join(item.symbols),
            item.interval,
            str(item.lookback_limit),
            str(item.max_holding_bars),
            str(item.trades),
            f"{item.win_rate:.2%}",
            f"{item.total_return_pct:.2f}%",
            f"{item.max_drawdown_pct:.2f}%",
            f"{item.risk_adjusted_score:.4f}",
        )
    console.print(table)
    if not plan.items:
        console.print(
            Panel(
                "No paper entries were queued because no sweep case passed the candidate filters.",
                title="Queue Empty",
                border_style="yellow",
            )
        )
    console.print(f"[green]Queue JSON:[/green] {json_path}")
    console.print(f"[dim]Queue Markdown:[/dim] {markdown_path}")


@app.command("crypto-hyperliquid-account")
def crypto_hyperliquid_account(
    wallet_address: Optional[str] = typer.Option(
        None,
        "--wallet-address",
        help="Hyperliquid user wallet address. Falls back to env config.",
    ),
    mainnet: bool = typer.Option(
        False,
        "--mainnet",
        help="Use https://api.hyperliquid.xyz instead of the default testnet URL.",
    ),
):
    """Show Hyperliquid clearinghouse account state."""

    from dataclasses import replace

    from tradingagents.crypto import CryptoTradingConfig, HyperliquidClient

    config = replace(CryptoTradingConfig.from_env(), exchange_provider="hyperliquid")
    if mainnet:
        config = replace(config, hyperliquid_testnet=False)
    if wallet_address:
        config = replace(config, hyperliquid_wallet_address=wallet_address)
    client = HyperliquidClient(config)
    state = client.get_user_state()
    margin = state.get("marginSummary", {})
    console.print(
        Panel(
            (
                f"account_value={margin.get('accountValue', '0')} | "
                f"margin_used={margin.get('totalMarginUsed', '0')} | "
                f"withdrawable={state.get('withdrawable', '0')}"
            ),
            title="Hyperliquid Account",
            border_style="cyan",
        )
    )
    table = Table(title="Open Positions", box=box.SIMPLE_HEAVY)
    table.add_column("Coin", style="bold")
    table.add_column("Size", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Unrealized PnL", justify="right")
    for item in state.get("assetPositions", []):
        position = item.get("position", {})
        table.add_row(
            str(position.get("coin", "")),
            str(position.get("szi", "")),
            str(position.get("entryPx", "")),
            str(position.get("unrealizedPnl", "")),
        )
    console.print(table)


@app.command("crypto-github-absorption")
def crypto_github_absorption(
    json_output: Optional[Path] = typer.Option(
        None,
        "--json-output",
        help="Optional path to write the full seven-source adoption map as JSON.",
    ),
):
    """Show the GitHub projects absorbed into the crypto roadmap."""

    from tradingagents.crypto import adoption_sources

    sources = adoption_sources()
    table = Table(title="Crypto GitHub Absorption Map", box=box.SIMPLE_HEAVY)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Adoption Target")
    table.add_column("Next Step")
    table.add_column("Guardrail")
    for source in sources:
        table.add_row(
            source.name,
            source.status,
            source.adoption_target,
            source.next_step,
            source.guardrail,
        )
    console.print(table)

    if json_output:
        payload = {
            "source_count": len(sources),
            "sources": [source.to_dict() for source in sources],
        }
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Adoption map JSON:[/green] {json_output}")


@app.command("crypto-base")
def crypto_base():
    """Show the TradingAgents base-layer contract for this crypto extension."""

    from tradingagents.crypto.base_contract import describe_base_contract

    console.print(Panel(describe_base_contract(), title="TradingAgents Base", border_style="green"))


if __name__ == "__main__":
    app()
