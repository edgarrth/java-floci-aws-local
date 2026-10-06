package pe.axiz.settlement;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import pe.axiz.settlement.application.ports.out.LedgerPort;
import pe.axiz.settlement.application.service.SettlementService;
import pe.axiz.settlement.domain.SettlementEvent;

class SettlementServiceTest {
  @Test
  void forwardsValidatedEvent() {
    var p = mock(LedgerPort.class);
    var e =
        new SettlementEvent(
            "e1",
            UUID.randomUUID(),
            "merchant-demo",
            BigDecimal.ONE,
            "PEN",
            "AUTHORIZED",
            "AX1",
            1);
    when(p.accept(e)).thenReturn(true);
    assertThat(new SettlementService(p).settle(e)).isTrue();
    verify(p).accept(e);
  }

  @Test
  void rejectsUnknownSchema() {
    assertThatThrownBy(
            () ->
                new SettlementEvent(
                    "e1",
                    UUID.randomUUID(),
                    "merchant-demo",
                    BigDecimal.ONE,
                    "PEN",
                    "AUTHORIZED",
                    "AX1",
                    2))
        .isInstanceOf(IllegalArgumentException.class);
  }

  @Test
  void rejectsNonFinal() {
    assertThatThrownBy(
            () ->
                new SettlementEvent(
                    "e1",
                    UUID.randomUUID(),
                    "merchant-demo",
                    BigDecimal.ONE,
                    "PEN",
                    "UNKNOWN",
                    null,
                    1))
        .isInstanceOf(IllegalArgumentException.class);
  }
}
