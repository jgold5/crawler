"""Fixed vocabularies used by the redactors. Everything here is public, non-secret knowledge."""
from __future__ import annotations


def _words(s: str) -> frozenset[str]:
    return frozenset(s.split())


# Placeholders the redactors emit inside string literals / step text.
PLACEHOLDERS = _words("""
v text int number url email path xpath css secret string Name value doc p regex date json html selector name
attr param tag word
""")
VALUE_SUBTYPES = _words("auto-id hashed-class state-class semantic-class number word kebab camel")

HTML_TAGS = _words("""
a abbr address area article aside audio b bdi bdo blockquote body br button canvas caption cite code col colgroup
data datalist dd del details dfn dialog div dl dt em embed fieldset figcaption figure footer form frame frameset
h1 h2 h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label legend li link main map mark menu
meta meter nav noscript object ol optgroup option output p param picture pre progress q s samp section select
slot small source span strong sub summary sup svg table tbody td template textarea tfoot th thead time title tr
track u ul var video wbr path g use rect circle polygon line
""")

XPATH_FUNCS = _words("""
last position count id local-name namespace-uri name string concat starts-with contains substring-before
substring-after substring string-length normalize-space translate boolean not true false lang number sum floor
ceiling round text node comment processing-instruction
""")
XPATH_AXES = _words("""
ancestor ancestor-or-self attribute child descendant descendant-or-self following following-sibling namespace
parent preceding preceding-sibling self
""")
XPATH_OPERATOR_NAMES = _words("and or div mod")

# Attribute names that are kept verbatim in locator shapes. Anything else becomes "data-custom".
# Angular / Angular Material / AG Grid attribute names: framework vocabulary, not app details.
FRAMEWORK_ATTRS = _words("""
formcontrolname formgroupname formarrayname formcontrol ngmodel routerlink routerlinkactive mattooltip matinput
matsuffix matprefix matbadge cdkdrag cdkdroplist ref comp-id col-id row-id row-index row-business-key
""")
# Custom elements from UI libraries (Angular Material, CDK, AG Grid, PrimeNG, NG-ZORRO, Kendo, DevExtreme, Ionic).
FRAMEWORK_TAG_RX = r"(?:mat|cdk|mdc|ag|ng|p|nz|kendo|dx|ion)-[a-z0-9]+(?:-[a-z0-9]+)*|router-outlet"

SAFE_ATTRS = _words("""
data-testid data-test data-test-id data-qa data-cy data-automation-id role id name class type placeholder col-id
row-id row-index title href value for alt src label disabled checked selected readonly tabindex target rel lang
dir hidden multiple required maxlength autocomplete action method scope colspan rowspan form style
""")
TEST_ID_ATTRS = _words("data-testid data-test data-test-id data-qa data-cy data-automation-id")

CSS_PSEUDOS = _words("""
first-child last-child nth-child nth-last-child nth-of-type nth-last-of-type first-of-type last-of-type
only-child only-of-type not has is where hover focus focus-within active checked disabled enabled empty visible
before after root required optional read-only placeholder-shown selected contains
""")

# Common English / UI / testing words. Capitalized words outside this list become <Name> in step phrases.
SAFE_WORDS = _words("""
a about above access accept accepted account accounts action actions active add added address admin after again
alert all allow allowed already also amount an and any app appear appears application apply approve approved are
area as ask at attach attached attribute available back background bar base be been before below between blank
both box browser but button buttons by calendar can cancel cancelled cannot card cart cell cells change changed
check checkbox checked checkout choose clear click clicked close closed code collapse column columns comment
confirm confirmation contain contains content continue copy correct count create created current customer
customers dashboard data date day days default delete deleted description details dialog disabled display
displayed do does done down download drag drop dropdown each edit edited editor element elements email empty
enabled end enter entered environment equal error errors every exist exists expand expected export failed
field fields file filter filtered find first following for form from full get given go goes grid group has have
header heading help hidden hide home hover i id if import in info information input invalid is it item items
its key label language last leave left less line link list load loaded loading log logged login logout make
manager max menu message messages min missing modal more move must my name navigate new next no not note
notification number of off ok on one only open opened option options or order orders other out page pages
pagination panel password pending per pick place please popup present press previous price product products
profile quantity record records refresh reject rejected reload remove removed report reports required reset
result results return right role row rows save saved scenario screen scroll search second section see select
selected send set settings should show shown sign size skip sort sorted start status step submit submitted
success successful successfully summary switch tab table tabs text that the then there these this time title to
toast toggle top total true type uncheck under unique up update updated upload url user username users valid
value values verify view visible wait warning when where which will window with within without yes you your api ui id ok csv pdf json xml html sql http https into onto over across via am shows tick
untick approve approved deny denied opens enters types clicks sees selects chooses hovers waits appears
""")

# Standard (spec / library) attribute values kept verbatim in locator shapes.
ARIA_ROLES = _words("""
alert alertdialog application article banner button cell checkbox columnheader combobox complementary
contentinfo definition dialog directory document feed figure form grid gridcell group heading img link list
listbox listitem log main marquee math menu menubar menuitem menuitemcheckbox menuitemradio navigation none
note option presentation progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox
separator slider spinbutton status switch tab table tablist tabpanel term textbox timer toolbar tooltip tree
treegrid treeitem
""")
INPUT_TYPES = _words("""
text password checkbox radio submit button email number date datetime-local time month week search hidden file
tel url range color reset image
""")
AG_GRID_API = _words("""
ensureIndexVisible ensureColumnVisible ensureNodeVisible getDisplayedRowAtIndex getDisplayedRowCount forEachNode
forEachNodeAfterFilter forEachNodeAfterFilterAndSort setFilterModel getFilterModel setColumnFilterModel
onFilterChanged setGridOption getGridOption setRowData applyTransaction refreshCells redrawRows sizeColumnsToFit
autoSizeAllColumns autoSizeColumns getRowNode selectAll deselectAll getSelectedRows getSelectedNodes
startEditingCell stopEditing getColumnDefs setColumnDefs applyColumnState getColumnState setFocusedCell
getFocusedCell paginationGoToPage paginationGetCurrentPage showLoadingOverlay hideOverlay setQuickFilter
getModel getRenderedNodes getCellValue exportDataAsCsv setSortModel getSortModel isDestroyed
""")

# Common tag words kept verbatim (@smoke, @regression, ...). Other tags become @tag-N.
TAG_WORDS = _words("""
smoke regression sanity wip ignore ignored manual flaky skip slow fast e2e api ui critical high medium low
blocker major minor p0 p1 p2 p3 positive negative happy path edge case nightly daily weekly release prod
staging dev qa uat sit known issue bug todo pending disabled mobile desktop web chrome firefox safari edge
headless parallel serial isolated integration acceptance functional unit performance security accessibility a11y
orders order login logout grid admin user
""")

# --- identifier anonymization -------------------------------------------------------------------------------

ROLE_WORDS = _words("""
page pages po steps step stepdefs defs definitions definition glue helper helpers utils util base component
components modal dialog grid table row rows cell cells column columns col header input field fields button btn
link dropdown select checkbox radio toggle tab menu item items list label text icon locator locators by element
elements driver wait waits factory config hooks hook runner context world api client test tests object section
panel form view screen manager service provider builder container widget popup
""")
CLASSY_ROLES = ROLE_WORDS  # roles that may end a class name

VERBS = _words("""
click enter type fill select check uncheck open go to goto navigate wait for get set is has verify assert should
scroll hover drag upload download login logout log in out search sort filter edit save cancel delete submit
expand collapse find clear press close add remove update create read init build switch accept dismiss focus
double right choose toggle are can show hide refresh reload until on do make apply reset confirm load sign
""")
FILL_VERBS = _words("enter type fill set select check uncheck upload search filter sort edit choose")
CLICK_VERBS = _words("""
click open go goto navigate submit save cancel delete expand collapse hover drag scroll download login logout
log sign close press confirm apply reset add remove create
""")

GENERIC_STEMS = _words("""
username user password email login logout sign in up out search submit cancel save close ok yes no next previous
prev back home main nav navigation header footer sidebar error errors message messages title spinner loader
loading overlay first last all new current default common generic custom value values name id xpath css url
timeout seconds millis duration visible clickable present displayed enabled disabled selected ready loaded and
or of to for on at the a an with from count size index key keys filter sort page row cell column grid table data
status result results total action actions confirm confirmation notification toast alert popup modal dialog form
details summary settings profile dashboard logo language locale date time start end min max screenshot browser
window frame iframe tab scenario hooks env environment base instance attempts retry poll polling interval element
locator string number option options dropdown file path text label link button input field list item items
abstract driver wait page web ui by js script java util helper factory config element type remote local headless
chrome firefox edge safari grid hub capabilities options properties property is has get set click enter fill
""")

PACKAGE_GENERIC = _words("""
com org net io de uk co qa test tests testing automation framework ui e2e main java src app web pages page steps
step stepdefs stepdefinitions definitions glue utils util helpers helper base core common config hooks runner
runners components component grid grids support driver drivers factory context api models model data constants
features tools tool lib libs internal shared selenium cucumber bdd acceptance functional regression
""")

# Third-party package prefixes whose imports are kept verbatim (conventions, not secrets).
SAFE_IMPORT_PREFIXES = (
    "java.", "javax.", "jakarta.", "org.openqa.selenium", "io.cucumber", "cucumber.api", "org.junit", "junit.",
    "org.testng", "org.hamcrest", "org.assertj", "io.github.bonigarcia", "org.slf4j", "org.apache.logging.log4j",
    "org.apache.commons", "com.fasterxml.jackson", "lombok", "io.restassured", "org.awaitility",
    "com.google.common", "com.google.gson", "io.qameta.allure", "net.serenitybdd", "net.thucydides",
    "org.springframework", "com.codeborne.selenide", "com.aventstack", "io.appium", "org.json", "okhttp3",
    "dev.failsafe", "net.datafaker", "com.github.javafaker", "org.picocontainer", "com.google.inject",
)

JAVA_LANG_TYPES = _words("""
String Integer Long Short Byte Double Float Boolean Character Object Number Math System Thread Runnable Exception
RuntimeException Error Throwable IllegalArgumentException IllegalStateException UnsupportedOperationException
NullPointerException InterruptedException StringBuilder StringBuffer Class Enum Iterable Void Override Deprecated
SuppressWarnings FunctionalInterface SafeVarargs CharSequence Comparable AutoCloseable Record ThreadLocal
InheritableThreadLocal Runtime Process ProcessBuilder ClassLoader StackTraceElement Cloneable Appendable
IndexOutOfBoundsException NumberFormatException ClassCastException ArithmeticException AssertionError
""")

# Methods/fields of well-known APIs (JDK, Selenium, Cucumber, JUnit/TestNG/AssertJ) kept verbatim.
KNOWN_API_NAMES = _words("""
findElement findElements sendKeys click clear getText getAttribute getDomAttribute getDomProperty getCssValue
getTagName isDisplayed isEnabled isSelected submit until withTimeout pollingEvery ignoring withMessage
visibilityOfElementLocated visibilityOf elementToBeClickable presenceOfElementLocated invisibilityOfElementLocated
invisibilityOf textToBePresentInElement textToBePresentInElementLocated urlContains urlToBe titleIs titleContains
numberOfElementsToBe presenceOfAllElementsLocatedBy visibilityOfAllElementsLocatedBy stalenessOf attributeContains
attributeToBe frameToBeAvailableAndSwitchToIt alertIsPresent elementSelectionStateToBe and or not refreshed
get navigate to back forward refresh quit close manage timeouts implicitlyWait pageLoadTimeout scriptTimeout window
maximize setSize getWindowHandle getWindowHandles switchTo frame defaultContent alert accept dismiss
executeScript executeAsyncScript getScreenshotAs getCurrentUrl getTitle getPageSource moveToElement perform build
doubleClick contextClick dragAndDrop keyDown keyUp release clickAndHold scrollToElement scrollByAmount pause
selectByVisibleText selectByValue selectByIndex deselectAll getOptions getFirstSelectedOption
getAllSelectedOptions isMultiple xpath cssSelector id name className linkText partialLinkText tagName
initElements ofSeconds ofMillis ofMinutes addArguments setHeadless setCapability addPreference
setExperimentalOption Given When Then And But Before After BeforeStep AfterStep BeforeAll AfterAll ParameterType
DataTableType DocStringType asMaps asMap asList asLists cells row column height width isFailed getName getStatus
attach log embed assertEquals assertNotEquals assertTrue assertFalse assertNull assertNotNull assertThat
assertThrows fail isEqualTo isNotEqualTo isTrue isFalse isNull isNotNull contains containsExactly hasSize
isEmpty isNotEmpty startsWith endsWith equals equalsIgnoreCase hashCode toString format valueOf parseInt
parseLong parseDouble length trim strip split replace replaceAll toLowerCase toUpperCase isBlank substring
indexOf charAt matches sleep currentThread println printf print getProperty getenv currentTimeMillis nanoTime
stream map filter collect forEach toList size add put remove of asList empty isPresent orElse orElseThrow ifPresent
getMessage getCause printStackTrace info debug warn error trace getLogger getClass getSimpleName iterator next
hasNext keySet values entrySet getKey getValue containsKey join builder now plusDays minusDays
XPATH CSS ID NAME CLASS_NAME LINK_TEXT PARTIAL_LINK_TEXT TAG_NAME ENTER TAB ESCAPE RETURN BACK_SPACE DELETE
ARROW_DOWN ARROW_UP CONTROL SHIFT FILE BYTES BASE64 HOW USING out err length class this super
""")

# --- page-definition files (pages.py) -----------------------------------------------------------------------
# Generic schema vocabulary for per-page locator files. A mapping key made only of these words (camelCase /
# snake / kebab compounds, plurals allowed) is schema and kept verbatim; so is an enum-like value. Never add
# business words here: anything outside this list becomes a placeholder.
PAGE_SCHEMA_WORDS = _words("""
page base extend extends parent inherit inherits inherited inheritance menu path iframe frame element field
locator xpath css selector id name type by how using value grid column default key row header col sort sortable
editable cell template param wait timeout visible optional dropdown option textfield text button checkbox radio
date label tab modal dialog container child children client profile override variant common component section
url route title class tag link partial index position description desc required enabled disabled hidden
readonly read only write mandatory multi multiple single select selection input textarea area number numeric
integer int decimal float double string boolean bool currency percent percentage time datetime timestamp
calendar picker datepicker timepicker lookup autocomplete combo combobox list listbox table icon image img
toggle switch item menuitem nav navigation breadcrumb root before after next previous prev first last top
bottom left right footer body main content panel window popup overlay spinner loader loading load ready state
status mode format pattern regex mask placeholder hint tooltip message error warning info width height size
min max length limit count order group filter search source target action event click hover submit save
cancel close open expand collapse scroll drag drop upload download file locale language lang alias ref
reference include import extra config setting property attribute attr data meta version schema kind category
role aria test testid qa automation strategy locate find finder wrapper scope context display displayed shown
show hide formatter renderer editor pinned filterable resizable flex align alignment style is has on of to in
at for with and or no not all any per auto custom new primary secondary unique use dynamic static arg var
variable map mapping def definition true false yes null none expected actual result heading caption note
comment help enable disable check uncheck checked unchecked selected focus clear tenant brand global shared
local wildcard dom shadow handle into within inside under above below ancestor descendant sibling following
preceding contains equals starts ends match exact case ignore trim normalize retry poll polling interval delay
ms millis seconds sec step screen view form region block card tree node level depth expandable subheader
total subtotal summary pagination pager paging home anchor href method api endpoint uri query keyword
""")

# --- red-team hardening: third-party vocabulary is kept only when it is provably library vocabulary ----------

# Package roots whose *single-type* imports are third-party library vocabulary (kept verbatim). Wildcard imports
# from these roots only unlock names on KNOWN_TYPES; wildcard imports from anywhere else unlock nothing.
THIRD_PARTY_ROOTS = (
    "java.", "javax.", "jakarta.", "org.openqa.selenium.", "io.cucumber.", "org.junit.", "org.testng.",
    "org.hamcrest.", "org.assertj.", "lombok.", "com.fasterxml.jackson.", "io.restassured.", "org.apache.commons.",
    "org.slf4j.", "org.apache.logging.", "com.google.common.", "org.springframework.", "io.qameta.allure.",
    "org.json.",
)

# Well-known JDK / Selenium / Cucumber / JUnit / TestNG / Hamcrest / AssertJ / Lombok / Jackson / logging simple
# type names. Kept verbatim unless declared in the repo or explicitly imported from a non-allowlisted package.
KNOWN_TYPES = _words("""
List ArrayList LinkedList Map HashMap LinkedHashMap TreeMap SortedMap NavigableMap Set HashSet LinkedHashSet TreeSet
SortedSet Collection Collections Arrays Optional OptionalInt OptionalLong OptionalDouble Iterator ListIterator
Objects Random UUID Date Calendar GregorianCalendar TimeZone Locale Properties Scanner StringJoiner StringTokenizer
Queue Deque ArrayDeque PriorityQueue Stack Vector Hashtable EnumMap EnumSet Comparator Comparators
NoSuchElementException ConcurrentModificationException Base64 Timer TimerTask Entry BitSet Currency Formatter
Function BiFunction Supplier Consumer BiConsumer Predicate BiPredicate UnaryOperator BinaryOperator Callable
IntFunction ToIntFunction ToLongFunction ToDoubleFunction IntPredicate IntConsumer BooleanSupplier IntSupplier
Stream IntStream LongStream DoubleStream Collectors Collector StreamSupport
TimeUnit ConcurrentHashMap ConcurrentMap Executors ExecutorService ScheduledExecutorService Executor Future
CompletableFuture CountDownLatch Semaphore TimeoutException ExecutionException AtomicInteger AtomicLong
AtomicBoolean AtomicReference ThreadLocalRandom CopyOnWriteArrayList BlockingQueue LinkedBlockingQueue
Pattern Matcher PatternSyntaxException MatchResult
Duration Instant LocalDate LocalDateTime LocalTime ZonedDateTime OffsetDateTime ZoneId ZoneOffset DayOfWeek Month
Year YearMonth Period Clock ChronoUnit ChronoField DateTimeFormatter DateTimeParseException TemporalAdjusters
File FileInputStream FileOutputStream FileReader FileWriter BufferedReader BufferedWriter InputStream OutputStream
InputStreamReader OutputStreamWriter IOException FileNotFoundException UncheckedIOException PrintStream PrintWriter
Reader Writer Serializable Closeable ByteArrayInputStream ByteArrayOutputStream StringReader StringWriter
Path Paths Files StandardCharsets Charset StandardOpenOption StandardCopyOption DirectoryStream FileSystems
URL URI URLEncoder URLDecoder HttpURLConnection MalformedURLException URISyntaxException InetAddress HttpClient
HttpRequest HttpResponse BigDecimal BigInteger RoundingMode MathContext
SimpleDateFormat DecimalFormat NumberFormat ParseException MessageFormat DateFormat
Method Field Constructor Modifier InvocationTargetException Proxy InvocationHandler
Retention RetentionPolicy Target ElementType Documented Inherited Annotation Repeatable
Robot Toolkit AWTException KeyEvent Logger Level Handler ConsoleHandler LogRecord
Connection DriverManager ResultSet Statement PreparedStatement SQLException
WebDriver WebElement By Keys JavascriptExecutor TakesScreenshot OutputType Alert Dimension Point Rectangle Cookie
SearchContext WrapsDriver WrapsElement HasCapabilities Capabilities MutableCapabilities ImmutableCapabilities
NoSuchElementException NoSuchFrameException NoSuchWindowException NoAlertPresentException NoSuchSessionException
StaleElementReferenceException ElementClickInterceptedException ElementNotInteractableException
ElementNotVisibleException ElementNotSelectableException InvalidElementStateException InvalidSelectorException
UnhandledAlertException WebDriverException JavascriptException NotFoundException SessionNotCreatedException
ScriptTimeoutException UnreachableBrowserException MoveTargetOutOfBoundsException InvalidArgumentException
ChromeDriver ChromeOptions ChromeDriverService ChromiumDriver ChromiumOptions FirefoxDriver FirefoxOptions
FirefoxProfile FirefoxBinary GeckoDriverService EdgeDriver EdgeOptions SafariDriver SafariOptions
InternetExplorerDriver InternetExplorerOptions RemoteWebDriver RemoteWebElement DesiredCapabilities CapabilityType
LocalFileDetector PageFactory FindBy FindBys FindAll How CacheLookup AjaxElementLocatorFactory
DefaultElementLocatorFactory ElementLocatorFactory ElementLocator FieldDecorator DefaultFieldDecorator
ExpectedConditions ExpectedCondition WebDriverWait FluentWait Wait Sleeper Select ISelect Quotes Color Colors
Actions Action Sequence PointerInput WheelInput KeyInput Interaction Locatable Coordinates ShadowRoot WindowType
LogEntries LogEntry LogType LoggingPreferences PageLoadStrategy UnexpectedAlertBehaviour RelativeLocator DevTools
HasDevTools Augmenter EventFiringDecorator WebDriverListener WebDriverManager Platform Browser BrowserType
Given When Then And But Before After BeforeStep AfterStep BeforeAll AfterAll Scenario DataTable DocString
ParameterType DataTableType DocStringType DefaultParameterTransformer DefaultDataTableEntryTransformer
DefaultDataTableCellTransformer CucumberOptions Cucumber Status PendingException TypeRegistry
TypeRegistryConfigurer AbstractTestNGCucumberTests Suite IncludeEngines SelectClasspathResource
ConfigurationParameter SnippetType
Test Assert Assertions Assume Assumptions BeforeClass AfterClass BeforeEach AfterEach DisplayName Disabled Ignore
RunWith Tag Tags Nested ParameterizedTest ValueSource CsvSource MethodSource ExtendWith Rule ClassRule TestInfo
Timeout RepeatedTest TestInstance Order TestMethodOrder MethodOrderer TestWatcher ExpectedException
BeforeMethod AfterMethod BeforeSuite AfterSuite BeforeTest AfterTest BeforeGroups AfterGroups DataProvider
Parameters Listeners ITestResult ITestContext ITestListener ISuite ISuiteListener IRetryAnalyzer Reporter
SoftAssert Factory
MatcherAssert Matchers CoreMatchers SoftAssertions AbstractAssert ThrowableAssert
Getter Setter Data Builder Value NoArgsConstructor AllArgsConstructor RequiredArgsConstructor Slf4j Log4j2 Log
ToString EqualsAndHashCode SneakyThrows NonNull Singular Accessors With Cleanup Synchronized Delegate SuperBuilder
ObjectMapper JsonNode ObjectNode ArrayNode JsonProperty JsonIgnore JsonIgnoreProperties JsonInclude JsonFormat
JsonCreator TypeReference JsonProcessingException DeserializationFeature SerializationFeature JsonAlias
JsonPropertyOrder JsonMappingException
LoggerFactory LogManager Marker
RestAssured RequestSpecification ValidatableResponse ContentType JsonPath RequestSpecBuilder ResponseSpecification
Response
ImmutableList ImmutableMap ImmutableSet Lists Maps Sets Strings Preconditions Joiner Splitter Stopwatch
StringUtils RandomStringUtils FileUtils IOUtils CollectionUtils NumberUtils ObjectUtils ArrayUtils RandomUtils
DateUtils
Allure Step Attachment Description Epic Feature Story Severity SeverityLevel Owner Link Issue TmsLink Flaky
Autowired Component Configuration Bean ContextConfiguration SpringBootTest Scope Qualifier Lazy Primary
JSONObject JSONArray JSONException Awaitility
""")

# ARIA 1.2 states and properties (spec vocabulary). Any other aria-* name is treated as a custom attribute.
ARIA_ATTRS = _words("""
aria-activedescendant aria-atomic aria-autocomplete aria-braillelabel aria-brailleroledescription aria-busy
aria-checked aria-colcount aria-colindex aria-colindextext aria-colspan aria-controls aria-current
aria-describedby aria-description aria-details aria-disabled aria-dropeffect aria-errormessage aria-expanded
aria-flowto aria-grabbed aria-haspopup aria-hidden aria-invalid aria-keyshortcuts aria-label aria-labelledby
aria-level aria-live aria-modal aria-multiline aria-multiselectable aria-orientation aria-owns aria-placeholder
aria-posinset aria-pressed aria-readonly aria-relevant aria-required aria-roledescription aria-rowcount
aria-rowindex aria-rowindextext aria-rowspan aria-selected aria-setsize aria-sort aria-valuemax aria-valuemin
aria-valuenow aria-valuetext
""")

# Words AG Grid uses in its structural / theme class names (ag-row, ag-header-cell, ag-theme-quartz-dark ...).
AG_GRID_WORDS = _words("""
grid root wrapper body viewport center cols col column columns container header headers row rows cell cells pinned
left right full width overlay loading no panel theme alpine balham material quartz dark auto fresh blue bootstrap
odd even first last focus focused selected selection hover value label text icon icons checkbox input group groups
expanded contracted expand contract filter filters floating menu popup tab tabs sort sorted ascending descending
none indicator resize resizer sticky top bottom spanning span level leaf child children drag handle animation paging
page pagination summary status bar side buttons button tool tools list option options editor editing edit inline
large number date picker range chart layout normal print scroll scrollbar horizontal vertical virtual invisible
hidden visible ltr rtl position absolute relative unselectable selectable disabled enabled align aligned highlight
changed data flash component angular react vue details detail master tree toggle chevron aria description spacer
fake scroller clipper stub opened closed open close active inactive invalid valid empty ghost dragging moving
placeholder wrapping wrap ellipsis tooltip keyboard focusable pill pills sidebar side tool toolpanel row-group
""")

# Words that may follow a UI-library tag prefix (mat-, cdk-, mdc-, ag-, ng-, p-, nz-, kendo-, dx-, ion-) for the
# custom element to count as library vocabulary (mat-select, p-dropdown, kendo-grid, ion-button ...).
FRAMEWORK_TAG_WORDS = _words("""
select option optgroup form field label hint error prefix suffix input checkbox radio group button buttons toggle
slide slider chip chips list grid tile card header title subtitle content actions footer avatar icon menu item
trigger sidenav drawer toolbar tooltip dialog tab tabs nav bar link panel expansion accordion stepper step table
row cell column def paginator sort spinner progress datepicker date range calendar autocomplete divider badge tree
node virtual scroll viewport overlay pane container outlet router template data dropdown multiselect inputtext
inputnumber inputswitch inputmask password textarea editor fileupload upload image carousel galleria dataview
picklist orderlist treetable timeline splitter skeleton toast messages message confirm confirmdialog popup sidebar
menubar breadcrumb steps tabview tabmenu splitbutton speeddial tag rating knob colorpicker cascader transfer layout
sider spin result empty statistic descriptions popconfirm popover drop down picker time number switch segmented
space mention anchor affix comment alert notification modal collapse typography text view scheduler chart gauge
sparkline combobox combo box listbox multicolumn maskedtextbox numerictextbox textbox dropdownlist datagrid lookup
selectbox tagbox check radiogroup angular root app page refresher segment searchbar fab infinite reorder ripple back
split buttons col note thumbnail img sliding options loading action sheet backdrop datetime container content
form-field selection filter search searchbox pager pagination window treeview upload calendar
""")

# Hostname labels that are infrastructure words, not company / product names (never auto-denylisted).
HOST_GENERIC = _words("""
www www2 uat qa dev test tests testing staging stage stg prod production prd preprod pre sit int internal intranet
corp local localhost portal api apis app apps web login auth sso admin mail email smtp cdn static assets img images
media db svc service services cloud online secure my mobile beta alpha demo sandbox perf ci cd git wiki jira
confluence nexus jenkins k8s cluster node server host gateway proxy lb edge cache files docs help support status
shop store blog news home ws ftp vpn remote office ent enterprise global eu us uk na apac emea east west north south
central backend frontend ui web1 web2 app1 app2 data
""")

# File extensions: a dotted token ending in one of these is a file name, not a host.
FILE_EXTS = _words("""
html htm xhtml js mjs cjs ts tsx jsx json yaml yml xml md txt csv tsv pdf png jpg jpeg gif svg ico webp css scss
less java class jar war py rb go rs php jsp jspx aspx asp do action feature properties conf cfg ini toml env log
lock zip gz tar tgz sh bat ps1 exe dll so sql doc docx xls xlsx ppt pptx kt kts groovy gradle scala swift woff
woff2 ttf eot map min bak tmp out iml pom
""")

# Two-letter identifiers / package segments that are generic (anything else that short is anonymized: initials).
SHORT_OK = _words("""
id by el ok to go at in on of is up no ui js po qa io or an am do it my if as be me we us wd db os ms
""")

KNOWN_API_NAMES = KNOWN_API_NAMES | _words("""
emptyList emptyMap emptySet singletonList singleton unmodifiableList unmodifiableMap requireNonNull nonNull isNull
toArray addAll removeAll retainAll getOrDefault putIfAbsent computeIfAbsent compute merge ofNullable anyMatch
allMatch noneMatch findFirst findAny sorted distinct limit skip count joining toSet toMap groupingBy counting flatMap
mapToObj mapToInt range rangeClosed boxed reduce peek getSeconds toMillis toSeconds plusSeconds minusSeconds
plusMinutes plusHours plusMonths minusMonths getYear getMonth getDayOfMonth parse ofPattern readAllBytes
readAllLines readString writeString write exists createDirectories createFile getFileName toPath toFile
getAbsolutePath getParent newInstance getDeclaredField getDeclaredMethod invoke setAccessible getBytes compile
matcher find group lines chars isDigit isLetter contentEquals compareTo compareToIgnoreCase concat repeat
lastIndexOf toCharArray getClassLoader getResource getResourceAsStream close flush readLine load getAnnotation
isAnnotationPresent getRect getLocation getSize getShadowRoot deleteAllCookies getCookies addCookie getCookieNamed
deleteCookieNamed newWindow parentFrame activeElement fullscreen minimize getWrappedDriver getWrappedElement
visibilityOfAllElements elementToBeSelected numberOfWindowsToBe textMatches domPropertyToBe attributeToBeNotEmpty
presenceOfNestedElementLocatedBy visibilityOfNestedElementsLocatedBy invisibilityOfElementWithText
numberOfElementsToBeMoreThan numberOfElementsToBeLessThan scrollIntoView sendKeysToElement moveByOffset
dragAndDropBy getSourceTagNames getId getUri getLine transpose asMaps assertAll assertArrayEquals assertSame
assertNotSame assertIterableEquals assertTimeout assertDoesNotThrow assertEqualsNoOrder assertAllAndCollect
assertAlso as describedAs isGreaterThan isLessThan isGreaterThanOrEqualTo isLessThanOrEqualTo doesNotContain
isInstanceOf extracting hasMessage isBetween isNotBlank isBlank containsOnly containsAll hasSizeGreaterThan
isPositive isZero overridingErrorMessage equalTo containsString hasItem hasItems notNullValue nullValue greaterThan
lessThan instanceOf everyItem hasEntry hasKey anyOf allOf given then statusCode body extract response jsonPath
header headers contentType queryParam queryParams pathParam formParam baseUri basePath post put patch delete
asString prettyPrint readValue writeValueAsString readTree asText asInt asLong asBoolean path has convertValue
getLog isDebugEnabled isInfoEnabled setUp tearDown setup teardown start stop await atMost pollInterval untilAsserted
ignoreExceptions getDriver setDriver quitDriver getInstance init main run apply accept test call
SECONDS MILLISECONDS MINUTES HOURS DAYS NANOSECONDS MICROSECONDS UTF_8 US UK ENGLISH ROOT INFO WARNING SEVERE FINE
ALL OFF DEBUG WARN ERROR TRACE NONE NORMAL EAGER TRUE FALSE MAX_VALUE MIN_VALUE ZERO ONE TEN APPLICATION_JSON JSON
TEXT HTML XML ANY ARROW_LEFT ARROW_RIGHT ALT COMMAND HOME END PAGE_DOWN PAGE_UP SPACE META NULL CANCEL CLEAR INSERT
F5 BACKSPACE PASSED FAILED SKIPPED PENDING UNDEFINED AMBIGUOUS RUNTIME SOURCE METHOD TYPE FIELD PARAMETER
HALF_UP HALF_EVEN DOWN UP CEILING FLOOR ISO_LOCAL_DATE ISO_LOCAL_DATE_TIME ISO_DATE ISO_DATE_TIME UTC
value css xpath id name className tagName linkText partialLinkText how using groups priority description enabled
dependsOnMethods dependsOnGroups timeOut expected features glue tags plugin monochrome dryRun snippets order
alwaysRun dataProvider retryAnalyzer invocationCount threadPoolSize parallel regex useRegexp preferForRegexMatch
""")



def _inflect(verbs: frozenset[str]) -> frozenset[str]:
    """Base verbs plus regular -s / -ed / -ing forms (click, clicks, clicked, clicking)."""
    out = set(verbs)
    for v in verbs:
        stem = v[:-1] if v.endswith("e") else v
        out.add(v + ("es" if v.endswith(("s", "sh", "ch", "x", "z", "o")) else "s"))
        out.add(v + "d" if v.endswith("e") else (v[:-1] + "ied" if v.endswith("y") and v[-2:-1] not in "aeiou"
                                                    else v + "ed"))
        out.add(stem + "ing")
        if v.endswith("y") and v[-2:-1] not in "aeiou":
            out.add(v[:-1] + "ies")
    return frozenset(out)


# Common step phrasing: function words, UI / test verbs (with inflections) and UI nouns / adjectives. Generic
# English only: never product, company or business-domain words (a step phrase keeps nothing else).
STEP_FUNCTION_WORDS = _words("""
a an the this that these those there here where when while what which who whom whose why how all any both each
every few many much more most less least other others some such no nor not only own same so than too very can
cannot will just should would could might must shall may also again further then once if or and but because as
until of at by for with about against between into through during before after above below to from up down in
out on off over under am is are was were be been being have has had having do does did doing done i me my mine
myself we our ours us you your yours he him his she her hers it its they them their theirs one ones two three four
five six seven eight nine ten hundred thousand zero first second third last next previous new old different
another either neither yet still already even ever never always often sometimes usually soon later today tomorrow
yesterday now within without via per across along around behind beside besides beyond inside outside onto upon
toward towards near far back away together apart instead else whether s t let lets yes ok okay given
""")
STEP_VERBS = _words("""
accept activate add allow appear apply approve arrive ask assert assign attach become begin belong browse build call
cancel change check choose clear click close collapse come compare complete confirm contain continue copy count
create deactivate decline delete deny deselect disable disappear dismiss display download drag drop edit enable end
enter exist expand expect expire export fail fill filter find finish focus get greet hide highlight hover import
include input keep land leave link load lock log login logout look make mark match move navigate need note notice
open pass paste pause pick pin place populate prefill press preview print proceed provide publish put read receive
redirect refresh register reject reload remain remember remove rename render reopen replace reply report request
require reset resize restore retry return revert review run save schedule scroll search see seed select send set
setup share show sign skip sort start stay stop store submit succeed switch take tap tick toggle try type uncheck
undo unlink unlock unpin unselect untick update upload use validate verify view visit wait want work write zoom
""")
STEP_IRREGULAR = _words("""
saw seen went gone chose chosen took taken gave given got gotten made found sent kept left led wrote written ran
shown hidden began begun came brought built bought caught felt held known knew lost meant met paid said sold told
thought understood won
""")
STEP_NOUNS = _words("""
contact contacts address addresses phone number numbers amount amounts banner banners logo logos image images icon
icons submenu sidebar navbar breadcrumb breadcrumbs tooltip tooltips wizard tile tiles widget widgets chart charts
graph graphs overview history activity activities period range subtotal quantity quantities cost costs fee fees tax
taxes discount discounts currency currencies comment comments note notes document documents attachment attachments
session sessions category categories tag tags kind kinds level levels priority priorities color colors permission
permissions team teams member members owner owners preference preferences query queries entry entries detail state
states week weeks month months year years hour hours minute minutes second seconds date dates step steps stage
link links preview message messages notification notifications portal site website app application screen screens
inactive invisible incorrect wrong optional mandatory unavailable unselected unchecked expanded collapsed complete
incomplete canceled latest earliest upper lower primary secondary unique duplicate random specific ready busy
unsaved editable readonly locked unlocked enabled disabled checked selected different correct empty full partial
right left top bottom middle center centre start end beginning success error failure warning info data record
records field fields text texts value values option options list lists item items row rows column columns cell
cells table tables grid grids form forms button buttons page pages tab tabs menu menus panel panels dialog dialogs
popup popups modal modals toast toasts header headers footer footers title titles label labels input inputs
dropdown dropdowns checkbox checkboxes radio toggle toggles spinner loader file files folder folders
""")

# Words a step phrase may keep verbatim (feature step text, table headers and step-definition patterns).
STEP_VOCAB = (SAFE_WORDS | GENERIC_STEMS | ROLE_WORDS | _inflect(VERBS | STEP_VERBS) | STEP_FUNCTION_WORDS
              | STEP_IRREGULAR | STEP_NOUNS)
# Words an identifier may consist of and still be "generic" (what idents.Anonymizer keeps unanonymized), plus the
# stems the anonymizer itself emits (Type3, item4, field5Input, action6Button, arg7, pkg8).
IDENT_GENERIC = GENERIC_STEMS | VERBS | ROLE_WORDS | _words("type item field action arg pkg external example x")
