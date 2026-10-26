@GLX-881 @smoke @wire
Feature: Globex wire transfers to Initech accounts
  Wire transfers above $10,000 require Hank Scorpio's approval.

  Background:
    Given Globex customer "montgomery.burns@springfield-power.com" is logged in

  Scenario: Transfer $5,000 to Initech payroll
    When the user enters "5000.00" into the "Wire amount (USD)" field
    And the user picks beneficiary "Initech Payroll - Acct 9876543210"
    And the user clicks "Send wire"
    Then the available balance should be "$1,234,567.89"

  Scenario Outline: Ledger edits for VIP accounts
    When the user sets "<column>" to "<value>" for account "<acct>" in the ledger
    Examples:
      | column   | value | acct         |
      | memo     | Q3 bonus for Peter Gibbons | 4400-1234-5678 |
      | category | Payroll | 4400-8765-4321 |
