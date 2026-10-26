Feature: Broken
  Scenario: Many problems
    Given I am logged in
    When I clik "Save"
    And I teleport to Mars
    Then I should see "ok"
    And I fly away
