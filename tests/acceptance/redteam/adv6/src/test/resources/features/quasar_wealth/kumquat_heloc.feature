# Owner: jane.roe, Quasar desk
@heloc @QuasarWealth @QW-1042 @kumquat_premier @smoke @owner=jane @regression_heloc @Smoke
Feature: Quasar Wealth heloc
  Quasar desk description line with kumquat

  Background: Kumquat login
    Given heloc customers exist
    And the "Quasar" desk is open
    """json
    {"client": "kumquatbank", "plan": "Quasar Gold"}
    """

  @jumbo
  Rule: Jumbo refinance rule
    Background:
      Given rule bg

    @QW-77
    Scenario Outline: Kumquat <plan> outline
      Given the <Quasar Plan> plan for <heloc_tier> and <customerName> is chosen
      And I enter <plan>

      @kumquat
      Examples: Quasar examples
        | Quasar Plan | heloc_tier | customerName | plan  |
        | Gold        | jumbo      | Jane Roe     | Plat  |

  Scenario: plain
    * Quasar wealth heloc margin call is shown
    Then the account 4400-1234-5678 for jane.roe@kumquatbank.bank shows 1,234,567.89
    And the IBAN GB33BUKB20201555555555 and sortcode 20-20-15 are shown
    And the user at 10.42.7.19:8443 and host kumquat-db01.prod.kq sees it
    And the token zq_tok_9f8e7d6c5b4a3210 is used
    And the key AKIAIOSFODNN7EXAMPLE is used
    And the Kumquat's offer and O'Brien are shown
