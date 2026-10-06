package pe.axiz.settlement;

import static org.assertj.core.api.Assertions.*;

import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import pe.axiz.platform.configuration.AwsConfiguration;
import pe.axiz.settlement.adapters.out.aws.DynamoAuditStore;
import pe.axiz.settlement.domain.PaymentAudit;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.DynamoDbException;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.SigningAlgorithmSpec;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;
import software.amazon.awssdk.services.sns.SnsClient;
import software.amazon.awssdk.services.ssm.SsmClient;
import software.amazon.awssdk.services.ssm.model.SsmException;

@EnabledIfEnvironmentVariable(named = "RUN_FLOCI_IT", matches = "true")
class FlociIntegrationTest {
  @Test
  void publishesValidRejectedEventWithPaymentRole() {
    try (var context = context("payment")) {
      String id = UUID.randomUUID().toString();
      String payload =
          "{\"eventId\":\""
              + id
              + "-final\",\"paymentId\":\""
              + id
              + "\",\"merchantId\":\"merchant-demo\",\"amount\":1.00,\"currency\":\"PEN\","
              + "\"status\":\"REJECTED\",\"reason\":\"SDK_VERIFICATION\",\"schemaVersion\":1}";
      String region = context.getEnvironment().getRequiredProperty("axiz.aws.region");
      var response =
          context
              .getBean(SnsClient.class)
              .publish(
                  request ->
                      request
                          .topicArn("arn:aws:sns:" + region + ":000000000000:axiz-payments.fifo")
                          .message(payload)
                          .messageGroupId(id)
                          .messageDeduplicationId(id));
      assertThat(response.messageId()).isNotBlank();
    }
  }

  @Test
  void signsReceiptWithSettlementRoleAndRealSdk() {
    try (var context = context("settlement")) {
      var response =
          context
              .getBean(KmsClient.class)
              .sign(
                  request ->
                      request
                          .keyId("alias/axiz-receipts")
                          .message(SdkBytes.fromUtf8String("sdk-verification"))
                          .signingAlgorithm(SigningAlgorithmSpec.RSASSA_PSS_SHA_256));
      assertThat(response.signature().asByteArray()).hasSize(256);
    }
  }

  private AnnotationConfigApplicationContext context(String service) {
    var context = new AnnotationConfigApplicationContext();
    String directory = System.getenv().getOrDefault("FLOCI_TEST_RUNTIME", ".runtime");
    context
        .getEnvironment()
        .getPropertySources()
        .addFirst(
            new MapPropertySource(
                "floci",
                Map.of(
                    "axiz.aws.region",
                    System.getenv().getOrDefault("AWS_REGION", "us-east-1"),
                    "axiz.aws.endpoint",
                    System.getenv().getOrDefault("AWS_ENDPOINT_URL", "http://localhost:4566"),
                    "axiz.aws.role-arn",
                    "arn:aws:iam::000000000000:role/axiz-" + service,
                    "axiz.aws.token-file",
                    directory + "/" + service + ".token",
                    "spring.application.name",
                    "integration-" + service,
                    "axiz.audit-table",
                    "axiz-payment-audit")));
    context.register(AwsConfiguration.class);
    context.refresh();
    return context;
  }

  @Test
  void persistsAndDeduplicatesKafkaAuditWithRealAwsSdk() {
    try (var context = context("settlement")) {
      var store =
          new DynamoAuditStore(context.getBean(DynamoDbClient.class), context.getEnvironment());
      var audit = new PaymentAudit(UUID.randomUUID().toString(), "{\"schemaVersion\":1}", 0, 7);
      store.record(audit);
      store.record(new PaymentAudit(audit.eventId(), audit.payload(), 0, 8));
      assertThat(store.find(audit.eventId())).contains(audit);
    }
  }

  @Test
  void rejectsAuditContentChangesWithRealConditionalWrite() {
    try (var context = context("settlement")) {
      var store =
          new DynamoAuditStore(context.getBean(DynamoDbClient.class), context.getEnvironment());
      var audit = new PaymentAudit(UUID.randomUUID().toString(), "first", 0, 7);
      store.record(audit);
      assertThatThrownBy(() -> store.record(new PaymentAudit(audit.eventId(), "changed", 0, 8)))
          .isInstanceOf(IllegalStateException.class);
    }
  }

  @Test
  void deniesPaymentAccessToSettlementData() {
    try (var context = context("payment")) {
      assertThatThrownBy(
              () ->
                  context
                      .getBean(DynamoDbClient.class)
                      .scan(request -> request.tableName("axiz-payment-audit")))
          .isInstanceOf(DynamoDbException.class)
          .satisfies(
              exception ->
                  assertThat(((DynamoDbException) exception).awsErrorDetails().errorCode())
                      .contains("Denied"));
    }
  }

  @Test
  void loadsMerchantPolicyWithWebIdentityCredentials() {
    try (var context = context("payment")) {
      assertThat(
              context
                  .getBean(SsmClient.class)
                  .getParameter(request -> request.name("/axiz/merchants/merchant-demo/limit"))
                  .parameter()
                  .value())
          .isEqualTo("5000.00");
    }
  }

  @Test
  void deniesSettlementAccessToMerchantConfiguration() {
    try (var context = context("settlement")) {
      assertThatThrownBy(
              () ->
                  context
                      .getBean(SsmClient.class)
                      .getParameter(request -> request.name("/axiz/merchants/merchant-demo/limit")))
          .isInstanceOf(SsmException.class)
          .satisfies(
              exception ->
                  assertThat(((SsmException) exception).awsErrorDetails().errorCode())
                      .contains("Denied"));
    }
  }

  @Test
  void retrievesSecretByRoleWithoutStaticKeys() {
    try (var context = context("payment")) {
      assertThat(
              context
                  .getBean(SecretsManagerClient.class)
                  .getSecretValue(request -> request.secretId("/axiz/api-key"))
                  .secretString())
          .hasSizeGreaterThan(20);
    }
  }
}
