"""
Report Generation Module
Creates synthetic inspection reports with different quality levels.

Generated reports are stored directly in the database (the single source of
truth) — no JSON side-files.
"""

from datetime import datetime
from typing import Dict, Any, Literal
import random
from openai import OpenAI

import config
from db_setup import init_db, get_session, Report
from qa_rules import QABaseline


class ReportGenerator:
    """Generates synthetic building inspection reports."""

    def __init__(self):
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        self.model = config.GENERATION_MODEL
        self.generator_version = config.GENERATOR_VERSION

        self.topics = [
            "vannskade på bad",
            "råte i takkonstruksjon",
            "setningsskader i grunnmur",
            "manglende ventilasjon på loft",
            "fukt i kjeller",
            "sprekker i fasade",
            "defekt elektrisk anlegg",
            "lekkasje fra tak"
        ]

    def _get_quality_instructions(self, quality: Literal["clean", "minor_error", "major_error"]) -> str:
        baseline = QABaseline.get_baseline()
        if quality == "clean":
            return f"""
            Create a PERFECT inspection report following these rules exactly:
            - Include ALL sections in this exact order: {', '.join(baseline['required_sections'])}
            - Use professional, technical language
            - Include specific measurements with units (m², %, kr, etc.)
            - Avoid vague words like: {', '.join(baseline['forbidden_words'])}
            - Each section should be 50-500 characters
            - Use bullet points in 'observasjoner' and 'anbefalinger'
            - Include specific cost estimates in 'kostnadsestimat'
            - Maintain logical consistency between severity and recommendations
            """
        elif quality == "minor_error":
            return f"""
            Create an inspection report with MINOR issues:
            - Include all required sections: {', '.join(baseline['required_sections'])}
            - Occasionally use vague language like "litt fuktig"
            - Sometimes forget units
            - Mix professional tone with casual phrases
            - Add 1–2 small inconsistencies
            """
        else:
            return f"""
            Create an inspection report with MAJOR issues:
            - Skip or reorder sections
            - Use vague, contradictory language
            - Include forbidden words: {', '.join(random.sample(baseline['forbidden_words'], 3))}
            - Make sections too short or too long
            """

    def save_to_database(self, filename, topic, status, text):
        """Save a generated report into the database."""
        session = get_session()
        report = Report(
            filename=filename,
            topic=topic,
            status=status,
            source="generated",
            report_text=text,
            model=self.model,
            generator_version=self.generator_version,
        )
        session.add(report)
        session.commit()
        session.close()

    def generate_report(self, topic: str, quality: Literal["clean", "minor_error", "major_error"], iteration: int = 1) -> Dict[str, Any]:
        instructions = self._get_quality_instructions(quality)
        prompt = f"""
        You are a Norwegian building inspector writing a report about: {topic}
        {instructions}
        Write the report in Norwegian with clear section headers.
        """

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are an experienced building inspector in Norway."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1500
        )

        report_text = response.choices[0].message.content
        return {
            "topic": topic,
            "status": quality,
            "report_text": report_text,
        }

    def save_report(self, report_data: Dict[str, Any]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        topic_slug = report_data["topic"].replace(" ", "_")[:20]
        filename = f"report_{topic_slug}_{report_data['status']}_{timestamp}.json"

        self.save_to_database(
            filename, report_data["topic"], report_data["status"], report_data["report_text"]
        )
        print(f"Saved report: {filename} (status: {report_data['status']})")
        return filename

    def batch_generate(self, count: int = 10) -> None:
        print(f"Generating {count} reports...")
        quality_distribution = {"clean": 0.4, "minor_error": 0.4, "major_error": 0.2}

        for i in range(count):
            topic = random.choice(self.topics)
            quality = random.choices(list(quality_distribution.keys()), weights=list(quality_distribution.values()))[0]
            try:
                report = self.generate_report(topic, quality, iteration=i + 1)
                self.save_report(report)
            except Exception as e:
                print(f"Error generating report {i+1}: {e}")
        print("Generation complete. Reports saved to the database.")


def main():
    if not config.OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY not found in .env file")
        return
    session = init_db()
    session.close()
    generator = ReportGenerator()
    generator.batch_generate(count=5)


if __name__ == "__main__":
    main()
