Feature: Book an equity swap trade
  Scenario: Enter a trade for Goldman Sachs
    When user enters "2026-01-02" in the "Trade Date" field
    And user enters "4.25" in the "Borrow Rate" field
    And user clicks the "Save" button
