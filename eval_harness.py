"""
Calibration harness for the QA judge.

A small set of labelled reports with known defects and the verdict each *should*
get. Run it against a real provider to check the judge discriminates (catches
genuine problems without over-flagging good reports):

    LLM_PROVIDER=openai python eval_harness.py
    LLM_PROVIDER=agent  python eval_harness.py

Exits non-zero if any case lands outside its accepted verdicts. Not part of the
offline pytest suite because it needs an LLM provider; use it as a manual
regression check when changing the judge prompt or rules.
"""

import sys

from db_setup import get_session, seed_rag_examples, seed_regulations
from judge import get_judge
from qa_engine import QAEngine
from rag_pipeline import RAGPipeline


def _report(sections) -> str:
    return "\n\n".join(f"{h}\n{body}" for h, body in sections)


# (name, intended defect, accepted final verdicts, report)
CASES = [
    ("A missing section", "Kostnadsestimat absent", {"major_error"}, _report([
        ("Sammendrag", "Boligen er i akseptabel stand. Det er påvist fuktinntrengning i kjeller som krever tiltak."),
        ("Observasjoner", "- Fuktmåling i kjellervegg viser 22 % ved gulvnivå.\n- Saltutslag på betongvegg over ca. 1,5 m²."),
        ("Årsak", "Manglende drenering rundt grunnmur og defekt utvendig fuktsikring."),
        ("Konsekvenser", "Risiko for råteskader i tilstøtende trekonstruksjoner og redusert inneklima."),
        ("Anbefalinger", "- Etabler ny drenering rundt grunnmur.\n- Utbedre utvendig fuktsikring innen 6 måneder."),
    ])),
    ("B serious, no cost", "TG3 finding, no cost + weak rec", {"major_error"}, _report([
        ("Sammendrag", "Det er avdekket alvorlig råteskade i bærende takkonstruksjon. Forholdet vurderes som kritisk."),
        ("Observasjoner", "- Omfattende råte i to bærende takbjelker.\n- Nedbøyning i mønet på ca. 30 mm.\n- Fuktmåling i trevirke viser 28 %."),
        ("Årsak", "Langvarig lekkasje gjennom taktekking har gitt vedvarende fuktbelastning."),
        ("Konsekvenser", "Fare for svikt i bærende konstruksjon dersom tiltak ikke gjennomføres."),
        ("Anbefalinger", "- Forholdet bør ses nærmere på ved anledning."),
        ("Kostnadsestimat", "Kostnad er vanskelig å anslå på nåværende tidspunkt."),
    ])),
    ("C vague language", "vague instead of measurements", {"minor_error", "major_error"}, _report([
        ("Sammendrag", "Boligen fremstår stort sett i grei stand, men med enkelte forhold som bør følges opp."),
        ("Observasjoner", "- Det er noe fukt i kjelleren.\n- Litt misfarging på baderomsgulvet.\n- Ganske store sprekker i grunnmuren enkelte steder."),
        ("Årsak", "Trolig en kombinasjon av alder og manglende vedlikehold."),
        ("Konsekvenser", "Kan medføre ytterligere forverring over tid, særlig dersom fukt ikke utbedres."),
        ("Anbefalinger", "- Forholdene bør utbedres, men tidspunkt er ikke nærmere angitt."),
        ("Kostnadsestimat", "Anslått samlet kostnad omkring 40 000 kr."),
    ])),
    ("D severity mismatch", "serious damage, trivial rec + tiny cost", {"major_error"}, _report([
        ("Sammendrag", "Det er registrert betydelige setningsskader i grunnmur og gulv."),
        ("Observasjoner", "- Skråstilte gulv med fall opptil 25 mm over 3 m.\n- Gjennomgående sprekk i grunnmur, bredde 8 mm.\n- Skjeve dørkarmer."),
        ("Årsak", "Setninger i grunnen, sannsynligvis utilstrekkelig fundamentering."),
        ("Konsekvenser", "Omfattende og potensielt økende skadeutvikling i konstruksjonen."),
        ("Anbefalinger", "- Forholdet kan vurderes ved neste ordinære vedlikehold."),
        ("Kostnadsestimat", "Mindre beløp, anslagsvis 5 000 kr."),
    ])),
    ("E clean control", "good report — must NOT be over-flagged", {"clean", "minor_error"}, _report([
        ("Sammendrag", "Boligen er gjennomgående i god stand for sin alder. Ett forhold med fukt på bad krever oppfølging."),
        ("Observasjoner", "- Fuktmåling ved sluk på bad viser 19 % i nedre veggsone.\n- Misfarging over ca. 0,6 m².\n- Øvrige våtrom uten påviste avvik."),
        ("Årsak", "Begynnende svikt i overgang mellom membran og sluk."),
        ("Konsekvenser", "Begrenset risiko for fuktskade i tilstøtende konstruksjon dersom forholdet ikke utbedres."),
        ("Anbefalinger", "- Utbedre membran ved sluk innen 6 måneder.\n- Foreta ny fuktkontroll etter utbedring."),
        ("Kostnadsestimat", "Estimert utbedringskostnad 35 000 - 50 000 kr."),
    ])),
]


def main() -> int:
    seed_rag_examples()
    seed_regulations()
    engine = QAEngine(judge=get_judge(), rag=RAGPipeline(get_session()))

    failures = 0
    for name, defect, accepted, report in CASES:
        verdict = engine.evaluate(report)["final_quality"]
        ok = verdict in accepted
        failures += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name:22} got={verdict:12} "
              f"accepted={sorted(accepted)}  ({defect})")

    print(f"\n{len(CASES) - failures}/{len(CASES)} cases within expected range")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
