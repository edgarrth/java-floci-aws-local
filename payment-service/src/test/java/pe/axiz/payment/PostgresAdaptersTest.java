package pe.axiz.payment;

import static org.assertj.core.api.Assertions.*;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.postgresql.PostgreSQLContainer;
import pe.axiz.payment.adapters.out.postgresql.JdbcPaymentStore;
import pe.axiz.payment.domain.*;
import tools.jackson.databind.json.JsonMapper;

@EnabledIfEnvironmentVariable(named = "RUN_POSTGRES_IT", matches = "true")
class PostgresAdaptersTest {
  private static PostgreSQLContainer container;
  private JdbcTemplate jdbc;
  private JdbcPaymentStore store;

  @BeforeAll
  static void start() {
    container = new PostgreSQLContainer("postgres:18.6");
    container.start();
    Flyway.configure()
        .dataSource(container.getJdbcUrl(), container.getUsername(), container.getPassword())
        .locations("classpath:db/migration")
        .load()
        .migrate();
  }

  @AfterAll
  static void stop() {
    if (container != null) container.stop();
  }

  @BeforeEach
  void init() {
    var ds = new PGSimpleDataSource();
    ds.setURL(container.getJdbcUrl());
    ds.setUser(container.getUsername());
    ds.setPassword(container.getPassword());
    jdbc = new JdbcTemplate(ds);
    jdbc.execute("TRUNCATE outbox, payments CASCADE");
    store =
        new JdbcPaymentStore(
            jdbc,
            new TransactionTemplate(new DataSourceTransactionManager(ds)),
            JsonMapper.builder().build());
  }

  Payment accepted() {
    return new Payment(
        UUID.randomUUID(),
        "merchant-demo",
        new Money(new BigDecimal("25.50"), "PEN"),
        "tok_demo",
        "integration-key",
        "fingerprint",
        Payment.Status.ACCEPTED,
        "POLICY_ACCEPTED",
        Instant.parse("2026-01-01T00:00:00.123456789Z"));
  }

  @Test
  void paymentAndBothDestinationsArePersistedOnce() {
    var payment = accepted();
    var persisted = store.record(payment);
    assertThat(persisted).usingRecursiveComparison().ignoringFields("createdAt").isEqualTo(payment);
    // Se comprueba el redondeo a microsegundos y el replay de la fecha persistida.
    assertThat(persisted.createdAt()).isEqualTo(Instant.parse("2026-01-01T00:00:00.123457Z"));
    assertThat(store.record(payment)).isEqualTo(persisted);
    assertThat(store.find(payment.id())).contains(persisted);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM payments", Long.class)).isEqualTo(1L);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox", Long.class)).isEqualTo(2L);
    assertThat(jdbc.queryForList("SELECT destination FROM outbox", String.class))
        .containsExactlyInAnyOrder("SNS", "KAFKA");
  }

  @Test
  void failureDuringSecondDeliveryRollsBackEntirePayment() {
    jdbc.execute(
        "ALTER TABLE outbox ADD CONSTRAINT test_destination CHECK (destination <> 'KAFKA')");
    try {
      var payment = accepted();
      assertThatThrownBy(() -> store.record(payment)).isInstanceOf(RuntimeException.class);
      assertThat(store.find(payment.id())).isEmpty();
      assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM outbox", Long.class)).isZero();
    } finally {
      jdbc.execute("ALTER TABLE outbox DROP CONSTRAINT test_destination");
    }
  }
}
