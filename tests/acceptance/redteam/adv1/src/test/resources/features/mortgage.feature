@smoke
Feature: Stuff

  Scenario: one
    Given Quasar wealth plan is active
    When I open the "Quasar Wealth" portal
    And the user visits uat-quasar.zenith-internal.se/login
    And the user opens portal.kumquatbank.bank
    Then ZenithQuasarWealth dashboard shows "x"
    And the jumbo refinance for heloc customers at portal.zenithfin.bank is approved
    And the zenith-financial banner and ZENITH_FINANCIAL code are shown
    And I see the following:
      | Beneficiary | Overdraft Limit | Sort Code | Platinum Tier |
      | a           | b               | c         | d             |
    And the app is ready. Kumquat banner is shown
