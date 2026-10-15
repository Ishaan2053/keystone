// Load step 5 (spec §4): one statement per algorithm, each over Person and REVIEWED_FOR only.
// Every Person gets a score, including the people with no REVIEWED_FOR edge (each is a one-person community).
// pageRank follows the edge direction (reviewer -> author): it ranks people reviewed by central reviewers.
CALL algo.pageRank('Person', 'REVIEWED_FOR') YIELD node, score
SET node.pagerank = score;

CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['REVIEWED_FOR'], samplingSeed: 42}) YIELD node, score
SET node.betweenness = score;

// communityId is an internal node id: group by it, never show it (team keys are numbered in inferred_teams).
CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['REVIEWED_FOR']}) YIELD node, communityId
SET node.community = communityId;
