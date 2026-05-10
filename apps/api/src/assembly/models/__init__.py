"""SQLAlchemy ORM models for Assembly.

Importing this package side-effect-registers every model on `Base.metadata`
so Alembic autogeneration sees them all.
"""
from assembly.models.agent import Agent, AgentEdge
from assembly.models.behavioral_mechanism import (
    BehavioralMechanism,
    BeliefNetworkRule,
    MechanismApplicabilityRule,
    MechanismEvidenceLink,
    MechanismInitializationAudit,
    PersuasionStrategyTaxonomy,
    ResearchSource,
)
from assembly.models.calibration import CalibrationEvaluation, OutcomeObservation
from assembly.models.adapter_status import AdapterComplianceStatus
from assembly.models.evidence import EvidenceItem
from assembly.models.llm_log import LLMCallLog
from assembly.models.output import SimulationOutput
from assembly.models.persona import (
    AudienceRetrievalRun,
    PersonaCluster,
    PersonaClusterMembership,
    PersonaEvidenceLink,
    PersonaGraphEdge,
    PersonaOpinion,
    PersonaRecord,
    PersonaTrait,
    PopulationConstructionAudit,
    SourceRecord,
)
from assembly.models.persona_psychology import PersonaPsychologyTrait
from assembly.models.discussion import (
    DiscussionGroup,
    DiscussionPrivateBallot,
    DiscussionSession,
    DiscussionTurn,
    PersonaMemoryAtom,
)
from assembly.models.cohort import (
    SocietyCohort,
    SocietyCohortEvidenceLink,
    SocietyCohortRollup,
)
from assembly.models.intent import (
    SimulatedIntent,
    SimulatedIntentRollup,
    SocietyArgument,
    SocietyArgumentPropagation,
)
from assembly.models.assembly_run import (
    AssemblyRun,
    AssemblyRunArtifact,
)
from assembly.models.round import AgentResponse, DebateTurn, SimulationRound
from assembly.models.simulation import Simulation, SimulationInput

__all__ = [
    "AdapterComplianceStatus",
    "Agent",
    "AssemblyRun",
    "AssemblyRunArtifact",
    "AgentEdge",
    "AgentResponse",
    "AudienceRetrievalRun",
    "BehavioralMechanism",
    "BeliefNetworkRule",
    "CalibrationEvaluation",
    "DebateTurn",
    "DiscussionGroup",
    "DiscussionPrivateBallot",
    "DiscussionSession",
    "DiscussionTurn",
    "EvidenceItem",
    "LLMCallLog",
    "MechanismApplicabilityRule",
    "MechanismEvidenceLink",
    "MechanismInitializationAudit",
    "OutcomeObservation",
    "PersonaCluster",
    "PersonaClusterMembership",
    "PersonaEvidenceLink",
    "PersonaGraphEdge",
    "PersonaMemoryAtom",
    "PersonaOpinion",
    "PersonaPsychologyTrait",
    "PersonaRecord",
    "PersonaTrait",
    "PersuasionStrategyTaxonomy",
    "PopulationConstructionAudit",
    "ResearchSource",
    "Simulation",
    "SimulationInput",
    "SimulationOutput",
    "SimulationRound",
    "SimulatedIntent",
    "SimulatedIntentRollup",
    "SocietyArgument",
    "SocietyArgumentPropagation",
    "SocietyCohort",
    "SocietyCohortEvidenceLink",
    "SocietyCohortRollup",
    "SourceRecord",
]
