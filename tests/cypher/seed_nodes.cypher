// Seeds a minimal Issue, Person and Module for tests/test_second_tenant.py, one set per graph.
CREATE (:Issue:Node {key: $issue, title: $issue}),
       (:Person:Node {key: $person, login: $login}),
       (:Module:Node {key: $module, name: $module})
